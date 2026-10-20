"""评分条款跨通道(normal x scoring_item)确定性归并收口。

职责: 评分条款被 tender_extractor(normal 含分值要求)与 scoring_extractor(scoring_item)
 双通道重复提取时, 把 scoring_item 的结构化评分元数据回填到等价 normal 主行并删除
 冗余 scoring_item, 消除确认页展示重复与下游重复评分/重复预估。
不做: 不新建评分项(scoring_extractor 职责)、不做普通要求语义去重(dedup_stage 职责)、
 不改审查/评分判定口径; 不跨文档归并; 不删 normal 行(保分面检索, 防 回归)。

等价判定刻意采用"确定性 AND 多条件"从严防误并(同 理念):
 同 category + 满分相容 + 描述 bigram 相似度 >= 阈值。
归并方向: 保留 normal 主行(携带 canonical_key/subject_scope/sub_requirements 供下游分面检索),
 仅回填 score_weight/is_key_review/scoring_metadata 与来源页并集; requirement_kind 保持 normal
 (下游 reviewer 对 is_key_review 与 scoring_item 一视同仁, 零下游改动)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.review_contracts import parse_json_list, serialize_json_list
from app.core.extractor.scoring.scoring_relation_planner import (
    ScoringRelation,
    append_scoring_relation,
)
from app.models.requirement import Requirement

# 描述相似度阈值(字符 bigram Jaccard)。0.55 对"同一评分条款的不同措辞"达标,
# 对"同类别但不同评分项"(如业绩 vs 技术)不达标; 配合同 category+满分相容多条件,误并概率极低。
_SIMILARITY_THRESHOLD = 0.55
_SCORE_TOLERANCE = 0.01
# 名称可辨性阈值: 描述中等相似但名称明显不同(实测 整体建设 vs 实施计划 名称=0.11)
# → 判为"共享评分细则的不同评分因素", 不合并; 名称相似度 >= 此值才认作同项。
_NAME_SIM_THRESHOLD = 0.5
# 描述近乎一致阈值: 超过即视为重复(跨chunk重复/同段拆分), 名称不同也合并(兜底保 去重力)。
_NEAR_DUP_DESC_THRESHOLD = 0.85


@dataclass
class ReconcileReport:
    """归并收口结果(供日志/进度展示)。"""

    examined_scoring: int = 0
    merged_count: int = 0


def _category_value(req: Any) -> str:
    """取分类的稳定字符串值(枚举或字符串均兼容)。"""
    category = getattr(req, "category", None)
    return getattr(category, "value", category)


def _normalize(text: Optional[str]) -> str:
    """归一化文本: 去除空白与标点(中文为单词字符不被剔除), 小写。"""
    if not text:
        return ""
    return re.sub(r"[\s\W]", "", text).lower()


def _bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def bigram_similarity(a: Optional[str], b: Optional[str]) -> float:
    """字符 bigram Jaccard 相似度。比字符集合 Jaccard 更能区分中文长文本。"""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    ba, bb = _bigrams(na), _bigrams(nb)
    if not ba or not bb:
        return 0.0
    union = len(ba | bb)
    return (len(ba & bb) / union) if union else 0.0


def is_same_scoring_item(
    name_a: Optional[str],
    desc_a: Optional[str],
    name_b: Optional[str],
    desc_b: Optional[str],
    *,
    weight_a: Any = None,
    weight_b: Any = None,
) -> bool:
    """判定两条评分项是否为"同一项"(可合并去重), 而非"共享评分细则的不同评分因素"。

 根因: 评分明细表不同评分因素(整体建设/实施计划/数据初始化)常共享同一段评分细则
 文字("根据…方案进行评分…优秀得6分、良好[4,5]分…"), 仅靠描述 bigram(实测 0.66~0.70 ≥0.55)
 会被误并, 各自满分(各6分)被吞 → 评标总分凭空少分。难点: 同段档位拆分(业绩
 40万门槛 vs 400万满分, 实测 desc=0.61/name=0.2)与不同评分因素(desc=0.66/name=0.11)在
 描述/名称相似度上高度重叠, 单一阈值无法区分。

 关键区分信号=满分归属:
 - 一方无有效满分(<=0/None): 是同一评分段的门槛/碎片, 描述达门槛即合并。
 - 双方各有有效满分: 多为不同评分因素(各自满分), 须名称可辨为同项(含括/相似度达标)或描述
 近乎一致才合并; 名称明显不同(整体建设 vs 实施计划)即判不同项, 不合并。
 """
    desc_sim = bigram_similarity(desc_a, desc_b)
    if desc_sim < _SIMILARITY_THRESHOLD:
        return False
    if desc_sim >= _NEAR_DUP_DESC_THRESHOLD:
        return True
    # 一方无有效满分 → 同段档位/碎片, 合并(同段异名拆分)
    if _coerce_weight(weight_a) is None or _coerce_weight(weight_b) is None:
        return True
    # 双方各有有效满分 → 默认不同评分因素, 仅名称可辨为同项才合并(防误并)
    na, nb = _normalize(name_a), _normalize(name_b)
    if not na or not nb:
        return True
    if na in nb or nb in na:
        return True
    return bigram_similarity(name_a, name_b) >= _NAME_SIM_THRESHOLD


def _coerce_weight(raw: Any) -> Optional[float]:
    """转有效满分: 非数字或 <=0 视为"无有效分值"返回 None。"""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def normal_weight_compatible(normal_weight: Any, scoring_weight: Any) -> bool:
    """normal 主行满分与 scoring_item 满分是否相容。

    normal 侧无有效满分(仅 is_key_review)视为相容; 两侧均有有效满分时须近似相等。
    """
    nw = _coerce_weight(normal_weight)
    if nw is None:
        return True
    sw = _coerce_weight(scoring_weight)
    if sw is None:
        return True
    return abs(nw - sw) <= _SCORE_TOLERANCE


def _scoring_lot(req: Any) -> Optional[str]:
    """取评分元数据中的采购包/标段标识(无则 None)。"""
    meta = getattr(req, "scoring_metadata", None) or {}
    lot = meta.get("lot") if isinstance(meta, dict) else None
    return str(lot) if lot else None


def _scoring_scheme(req: Any) -> Optional[str]:
    """取评分元数据中的评分方案标识(无则 None)。"""
    meta = getattr(req, "scoring_metadata", None) or {}
    scheme = meta.get("scheme") if isinstance(meta, dict) else None
    return str(scheme) if scheme else None


def _scoring_group_label(req: Any) -> Optional[str]:
    """取归一化的原文组标签(无则 None)。换轴 P2-2: 组标签为一等物理证据。"""
    from app.core.extractor.scoring.ledger.label_normalization import normalize_group_label

    meta = getattr(req, "scoring_metadata", None) or {}
    label = meta.get("volume_label") if isinstance(meta, dict) else None
    normalized = normalize_group_label(str(label)) if label else ""
    return normalized or None


def is_equivalent_scoring_clause(
    normal_req: Requirement,
    scoring_req: Requirement,
    *,
    semantic_similarity: float = 0.0,
) -> bool:
    """判定 normal 评分条款与 scoring_item 是否为同一评分条款(确定性 AND 多条件)。: 描述等价轴在 bigram 之外复用普通通道候选门的 embedding 余弦
 (`semantic_similarity`, 阈值同 TENDER_CANDIDATE_SIM_THRESHOLD)。normal 是模型改写句、
 scoring_item 是评分表原文，字符 bigram 对改写不稳定(实测 3560 五对全部漏配)。
 lot/scheme/组标签/类别/满分相容守卫不变，防误并边界不放宽。
 """
    # 跨采购包禁止归并——多包文档两包常有同名同分评分项(如各自的"技术负责人3分"),
    # 描述相似度会达标; normal 主行一旦被先归并的包占据 lot, 后一包的等价项再并入会被
    # 静默删除吞分(回填不覆盖已有 weight/metadata)。lot 双方已知且不同即判不等价。
    normal_lot = _scoring_lot(normal_req)
    scoring_lot = _scoring_lot(scoring_req)
    if normal_lot and scoring_lot and normal_lot != scoring_lot:
        return False
    # 跨评分方案同理禁止归并(多套百分制常有同名同分项)。
    normal_scheme = _scoring_scheme(normal_req)
    scoring_scheme = _scoring_scheme(scoring_req)
    if normal_scheme and scoring_scheme and normal_scheme != scoring_scheme:
        return False
    # 换轴 P2-2: 跨原文组标签禁止归并——组标签是一等物理证据(分组标题行/表级标题),
    # 同名同分但分属不同原文组的条款不得关联(守卫在标签轴的推广)。
    normal_label = _scoring_group_label(normal_req)
    scoring_label = _scoring_group_label(scoring_req)
    if normal_label and scoring_label and normal_label != scoring_label:
        return False
    if _category_value(normal_req) != _category_value(scoring_req):
        return False
    if not normal_weight_compatible(
        getattr(normal_req, "score_weight", None),
        getattr(scoring_req, "score_weight", None),
    ):
        return False
    if (
        bigram_similarity(normal_req.description, scoring_req.description)
        >= _SIMILARITY_THRESHOLD
    ):
        return True
    return semantic_similarity >= settings.TENDER_CANDIDATE_SIM_THRESHOLD


def _merge_pages(*raw_sources: Any) -> list[int]:
    """合并多个来源页字段为升序去重整数列表。"""
    seen: set[int] = set()
    out: list[int] = []
    for raw in raw_sources:
        for token in parse_json_list(raw):
            try:
                page = int(token)
            except (TypeError, ValueError):
                continue
            if page >= 1 and page not in seen:
                seen.add(page)
                out.append(page)
    out.sort()
    return out


def _backfill_scoring_into_normal(normal: Requirement, scoring: Requirement) -> None:
    """把 scoring_item 的结构化评分信息回填到 normal 主行(逐字段补全, 不覆盖已有有效值)。"""
    if _coerce_weight(getattr(normal, "score_weight", None)) is None:
        sw = _coerce_weight(getattr(scoring, "score_weight", None))
        if sw is not None:
            normal.score_weight = sw
    normal.is_key_review = 1
    # 逐字段合并评分元数据(追修): 不能用"normal 无 metadata 才整体回填"的 all-or-nothing——
    # relevance_classifier 给评分相关 normal 标占位 {'source':'inline'}(truthy)会整体阻断回填,
    # 致卷归属(volume/volume_weight/volume_max)永不回填 → 业绩/专利等落入"其他"卷且确认页误报
    # 技术卷缺口。改为仅补 normal 缺失(None)字段, 已有有效值不覆盖(保 防误改)。
    scoring_meta = getattr(scoring, "scoring_metadata", None) or {}
    auth_desc = getattr(scoring, "description", None)
    if scoring_meta or auth_desc:
        merged = dict(getattr(normal, "scoring_metadata", None) or {})
        # lot 必须随满分一起回填——多包文档按 lot 分组各校验 100, 丢 lot 的回填行
        # 会被 lot 分组静默丢弃, 对应采购包被误报"漏提"(项目1553 包1 94.5 缺的 5.5 分即此)。
        # scheme 同理必须随满分回填, 防多方案文档回填行丢方案归属后混算。
        for key in (
            "volume", "volume_weight", "volume_max", "lot", "scheme",
            "max_score", "score_item_name", "score_rules", "deduction_rules",
        ):
            if merged.get(key) is None and scoring_meta.get(key) is not None:
                merged[key] = scoring_meta[key]
        # P0 A2: 保留评分表权威原文, 供下游 Prompt 消费。
        # 来源: scoring.description (评分表原始文本, 非 LLM 改写), 不取自 scoring_meta。
        # 不覆盖 normal.description(保分面检索语义); 仅在 merged 尚无权威原文时写入。
        if auth_desc and merged.get("authoritative_description") is None:
            merged["authoritative_description"] = auth_desc
        normal.scoring_metadata = merged
    normal.source_pages = serialize_json_list(
        _merge_pages(getattr(normal, "source_pages", None), getattr(scoring, "source_pages", None))
    )


SemanticLookup = dict[tuple[int, int], float]


async def _semantic_similarity_lookup(
    normal_clauses: list[Requirement], scoring_items: list[Requirement]
) -> SemanticLookup:
    """复用候选门的 embedding 入口计算 normal x scoring 描述余弦; 失败返回空表(仅剩 bigram)。"""
    if not normal_clauses or not scoring_items:
        return {}
    try:
        import numpy as np

        from app.core.extractor.tender_extractor_parts.candidate_gate import (
            _encode_texts_async,
        )

        texts = [r.description or "" for r in (*normal_clauses, *scoring_items)]
        matrix = await _encode_texts_async(texts)
        if matrix is None or len(matrix) != len(texts):
            return {}
        matrix = np.asarray(matrix, dtype=np.float32)
        matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
        sims = matrix[: len(normal_clauses)] @ matrix[len(normal_clauses):].T
        return {
            (normal.id, scoring.id): float(sims[i, j])
            for i, normal in enumerate(normal_clauses)
            for j, scoring in enumerate(scoring_items)
        }
    except Exception as exc:
        logger.warning("[评分项归并] embedding 相似度不可用, 退化为 bigram: {}", exc)
        return {}


def _best_normal_match(
    scoring: Requirement,
    normal_clauses: list[Requirement],
    semantic: SemanticLookup | None = None,
) -> Optional[Requirement]:
    """在 normal 评分条款候选中选与 scoring 等价且相似度最高者(同分优先主行)。"""
    best: Optional[Requirement] = None
    best_sim = -1.0
    lookup = semantic or {}
    for normal in normal_clauses:
        cosine = lookup.get((normal.id, scoring.id), 0.0)
        if not is_equivalent_scoring_clause(normal, scoring, semantic_similarity=cosine):
            continue
        sim = max(bigram_similarity(normal.description, scoring.description), cosine)
        prefer_main = (
            best is not None
            and abs(sim - best_sim) < 1e-9
            and getattr(normal, "main_requirement_id", None) is None
            and getattr(best, "main_requirement_id", None) is not None
        )
        if sim > best_sim or prefer_main:
            best, best_sim = normal, sim
    return best


def _fallback_cover_match(
    scoring: Requirement, normal_clauses: list[Requirement]
) -> Optional[Requirement]:
    """同包同类同分但描述被normal聚合时, 仅建立covers关系。"""
    scoring_lot = _scoring_lot(scoring)
    candidates: list[Requirement] = []
    for normal in normal_clauses:
        if _category_value(normal) != _category_value(scoring):
            continue
        if not normal_weight_compatible(
            getattr(normal, "score_weight", None),
            getattr(scoring, "score_weight", None),
        ):
            continue
        normal_lot = _scoring_lot(normal)
        if normal_lot and scoring_lot and normal_lot != scoring_lot:
            continue
        candidates.append(normal)
    return candidates[0] if len(candidates) == 1 else None


def _is_scoring_clause(req: Requirement) -> bool:
    """normal 行是否已被识别为评分条款(含分值或重点评审)。"""
    return _coerce_weight(getattr(req, "score_weight", None)) is not None or bool(
        getattr(req, "is_key_review", 0)
    )


async def reconcile_scoring_items(db: AsyncSession, document_id: int) -> ReconcileReport:
    """跨通道归并: scoring_item 命中等价 normal 主行则回填并删除该 scoring_item。

    仅处理未确认(confirmed=0)的 scoring_item, 避免触碰人工已确认成果。
    """
    report = ReconcileReport()
    rows = (
        await db.execute(
            select(Requirement)
            .where(Requirement.document_id == document_id)
            .order_by(Requirement.id)
        )
    ).scalars().all()

    normal_clauses = [
        r
        for r in rows
        if getattr(r, "requirement_kind", "normal") != "scoring_item" and _is_scoring_clause(r)
    ]
    scoring_items = [
        r
        for r in rows
        if getattr(r, "requirement_kind", "normal") == "scoring_item" and not bool(r.confirmed)
    ]
    report.examined_scoring = len(scoring_items)
    if not normal_clauses or not scoring_items:
        return report

    semantic = await _semantic_similarity_lookup(normal_clauses, scoring_items)
    for scoring in scoring_items:
        match = _best_normal_match(scoring, normal_clauses, semantic)
        relation_type = "equivalent"
        if match is None:
            match = _fallback_cover_match(scoring, normal_clauses)
            relation_type = "covers"
        if match is None:
            continue
        scoring_lot = _scoring_lot(scoring)
        linked_lots = (getattr(match, "scoring_metadata", None) or {}).get("linked_scoring_lots") or []
        if scoring_lot and linked_lots and scoring_lot not in linked_lots:
            continue
        append_scoring_relation(
            match,
            ScoringRelation(
                normal_id=match.id,
                scoring_id=scoring.id,
                relation_type=relation_type,  # type: ignore[arg-type]
                lot=scoring_lot,
            ),
        )
        report.merged_count += 1

    if not report.merged_count:
        return report

    await db.commit()
    logger.info(
        "[评分项归并] 文档 {} 建立 {} 条 scoring_item 到 normal 的非破坏关联(检视 {} 条)",
        document_id,
        report.merged_count,
        report.examined_scoring,
    )
    return report

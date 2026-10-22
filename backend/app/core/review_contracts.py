"""投标审查共享合同与工具函数。

收口审查流程中的阶段语义、页码/列表字段解析，以及结果汇总真源，
避免 service、reviewer、report 等模块各自维护一套口径。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy import or_

from app.core.config import settings
from app.models.requirement import Requirement
from app.models.review_result import ComplianceLevel, RiskLevel

# 审查流程阶段（对外状态）
REVIEW_STAGE_PENDING = "pending"
REVIEW_STAGE_EXTRACTING = "extracting_responses"
REVIEW_STAGE_ASSEMBLING = "assembling_pairs"
REVIEW_STAGE_REVIEWING = "reviewing_responses"
REVIEW_STAGE_RESULT_READY = "result_ready"
REVIEW_STAGE_COMPLETED = "completed"
REVIEW_STAGE_FAILED = "failed"

# 审查步骤标签（对前端和日志统一）
REVIEW_STEP_LABEL_EXTRACT = "投标响应提取"
REVIEW_STEP_LABEL_ASSEMBLE = "响应装配校验"
REVIEW_STEP_LABEL_REVIEW = "AI对比审查"
REVIEW_STEP_LABEL_READY = "结果就绪"

# 类别权重
CATEGORY_WEIGHTS = {
    "qualification": 0.25,
    "technical": 0.30,
    "commercial": 0.20,
    "performance": 0.15,
    "staffing": 0.05,
    "format": 0.05,
}

# 可进入审查的评审意义级别（可评审性闸门唯一权威值）
REVIEWABLE_RELEVANCE = "actionable"


def actionable_requirement_condition():
    """返回"评审意义可进入审查"的 SQLAlchemy 条件（闸门核心谓词）。

 职责: 收口"哪些要求参与响应提取/审查/计分"的唯一判定——review_relevance 为
 actionable，或属评分链路（scoring_item / is_key_review）。
 不做: 不判定 confirmed 状态（由 reviewable_requirement_conditions 组合）；
 不修改数据、不做人工确认语义。

 评分项铁律: 评分项(requirement_kind='scoring_item')与重点评审项(is_key_review=1)
 无条件通过闸门——评分项是审标流程的必审对象，即使 review_relevance 被任何
 路径误改也不得排除（用户明确约束，2026-07-05）。

 Returns:
 SQLAlchemy 布尔条件；闸门开关关闭时返回 None（调用方跳过该过滤）。
 """
    if not settings.REVIEW_ACTIONABLE_GATE_ENABLED:
        return None
    return or_(
        Requirement.review_relevance == REVIEWABLE_RELEVANCE,
        Requirement.requirement_kind == "scoring_item",
        Requirement.is_key_review == 1,
    )


def reviewable_requirement_conditions() -> tuple:
    """返回"可进入审查的已确认要求"的 SQLAlchemy 条件元组。

    供确认后链路（响应提取/匹配/审查启动/进度统计/重试推断）统一展开使用:
        select(Requirement).where(Requirement.project_id == pid,
                                  *reviewable_requirement_conditions())
    闸门关闭时退化为仅 (confirmed == 1,)，即旧行为。
    """
    base = (Requirement.confirmed == 1,)
    condition = actionable_requirement_condition()
    return base if condition is None else base + (condition,)


def is_reviewable_requirement(req: Any) -> bool:
    """行级判定一条要求是否可进入审查（与 SQL 条件语义严格一致）。

    Args:
        req: Requirement ORM 行或具备同名属性的对象。

    Returns:
        已确认且（闸门关闭 / actionable / 评分项 / 重点评审项）时为 True。
    """
    if not bool(getattr(req, "confirmed", 0)):
        return False
    if not settings.REVIEW_ACTIONABLE_GATE_ENABLED:
        return True
    if getattr(req, "requirement_kind", "normal") == "scoring_item":
        return True
    if bool(getattr(req, "is_key_review", 0)):
        return True
    relevance = getattr(req, "review_relevance", None) or REVIEWABLE_RELEVANCE
    return relevance == REVIEWABLE_RELEVANCE


def counts_toward_review_summary(
    *,
    review_relevance: Any,
    requirement_kind: Any,
    is_key_review: Any,
) -> bool:
    """判定一条已审查结果是否计入评分聚合（P2.2 聚合动态排除）。

 职责: 使评分对分类漏网项鲁棒——存量已审查行或事后被人工降级为参考条款的行，
 按 requirement 当前 review_relevance 从汇总/评分中排除，无需重审即修正评分。
 不做: 不过滤明细行（结果页保留展示并打"不计分"标识）、不改审查结论本身。

 评分项铁律: scoring_item / is_key_review 恒计入（评分项是审标必审对象，
 任何情况不得从计分中排除；用户明确约束，2026-07-05）。
 闸门开关关闭时全部计入（旧行为）。
 """
    if not settings.REVIEW_ACTIONABLE_GATE_ENABLED:
        return True
    if requirement_kind == "scoring_item":
        return True
    if bool(is_key_review):
        return True
    return (review_relevance or REVIEWABLE_RELEVANCE) == REVIEWABLE_RELEVANCE


def parse_page_numbers(raw: str | None) -> list[int]:
    """把逗号/短横线页码文本解析为去重升序页码列表。"""
    if not raw:
        return []

    pages: set[int] = set()
    for part in str(raw).split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, _, end_text = chunk.partition("-")
            if start_text.strip().isdigit() and end_text.strip().isdigit():
                start = int(start_text.strip())
                end = int(end_text.strip())
                if start <= end:
                    pages.update(range(start, end + 1))
                continue
        if chunk.isdigit():
            pages.add(int(chunk))
    return sorted(pages)


def serialize_page_numbers(pages: Sequence[int] | None) -> str | None:
    """把页码列表序列化为稳定的逗号文本。"""
    if not pages:
        return None
    normalized = sorted({int(p) for p in pages if isinstance(p, int) or str(p).isdigit()})
    return ",".join(str(p) for p in normalized) if normalized else None


def parse_json_list(raw: str | list[Any] | None) -> list[Any]:
    """兼容列表、JSON 字符串和空值。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
    return []


def serialize_json_list(values: Sequence[Any] | None) -> str | None:
    """把列表序列化为 JSON 文本。"""
    if not values:
        return None
    return json.dumps(list(values), ensure_ascii=False)


@dataclass
class ReviewSummaryItem:
    """用于统一汇总口径的轻量结果视图。"""

    category: str
    is_compliant: bool
    compliance_level: Any
    risk_level: Any
    score: int


@dataclass(frozen=True)
class CrossEvidence:
    """Fix-08: 跨类别证据结构化表达（替代纯字符串拼接）。

    每条命中记录包含：来源类别、snippet、source_pages、evidence_anchor，
    可用 :meth:`to_prompt_line` 生成带锚定的 Prompt 行。
    """

    category: str
    snippet: str
    pages: str
    anchor: str | None = None

    def to_prompt_line(self) -> str:
        from app.core.i18n import translate_token

        pages_raw = (self.pages or "").strip()
        if pages_raw:
            # 将 "34-44" / "34,35" / "34" 规范为「第…页」
            nums = [n for n in re.split(r"[\s,，、\-]+", pages_raw) if n]
            pages_label = ("第" + "、".join(nums) + "页") if nums else "页码未知"
        else:
            pages_label = "页码未知"
        category_label = translate_token(self.category) if self.category else "未知类别"
        # translate_token 未命中时回退原值；若仍是英文则加「类」后缀可读性
        if category_label == (self.category or ""):
            category_label = f"{self.category or '未知'}类"
        else:
            category_label = f"{category_label}类响应"
        anchor_part = f"·锚点:{self.anchor}" if self.anchor else ""
        return (
            f"[跨类别证据·来自{category_label}·{pages_label}"
            f"{anchor_part}] {self.snippet}"
        )


@dataclass
class ReviewEvidenceBundle:
    """审查阶段统一消费的证据包。

    Fix-02 新增 retrieval_* 三字段（均来自持久化的 bid_response_chunks 表），
    作为下游 Gate（PageAnchorVerifyGate 等）的事实参照源，不再依赖 LLM 自产
    source_pages / comment 文本。空表场景下装配方会降级为空集合并打 degraded 日志。

    Fix-08 新增 ``cross_category_evidence_sources``: 结构化跨类别命中列表；
    保留旧字段 ``cross_category_evidence: str`` 向后兼容，两者在
    :meth:`merged_evidence_text` 中汇总为 LLM Prompt 文本。
    """

    response_content: str
    source_pages: list[int]
    completeness: str | None
    missing_elements: list[str]
    linked_to: int | None
    evidence_type: str | None
    evidence_anchor: str | None
    relevance_score: int | None
    relevance_note: str | None
    image_evidence: str
    cross_category_evidence: str
    quality_flags: list[str]
    retrieval_chunk_pages: set[int] = field(default_factory=set)
    retrieval_primary_pages: set[int] = field(default_factory=set)
    retrieval_hit_sub_queries: list[str] = field(default_factory=list)
    cross_category_evidence_sources: list["CrossEvidence"] = field(default_factory=list)
    # 知识库上下文文本由 Phase1 短 session 内构建并随 evidence 传入,
    # _review_single_pair 不再持有 self.db 调用 KnowledgeContextBuilder,
    # 避免在 LLM 调用期间挂连接池连接 (违反 设计原则)。
    knowledge_context_text: str = ""
    # 为结果可观测性记录本对 LLM 实际可见的知识库引用; 仅项目级显式授权后才会
    # 非空, 用于 review_results.gate_trace 与前端"知识库辅助参考"展示。
    knowledge_context_refs: list[dict[str, Any]] = field(default_factory=list)
    # R1: 投标原文佐证摘录文本（来自 bid_response_chunks join chunks），由
    # ``prefetch_evidence`` 装配，``build_review_prompt`` 注入到 LLM 上下文，
    # 用于消除"审查只看摘要、不回看原文"的证据链断点。空字符串表示未注入
    # （查询失败/无命中/配置关闭）。
    raw_evidence_excerpts: str = ""
    # 挂载 chunk / 原文佐证拼成的证据包文本, 供 Claim 闸禁止「未提供」定谳
    evidence_pack_text: str = ""
    # 其他条款检索已登记的项目级账本（Prompt 文本 + 原子列表）
    ledger_evidence: str = ""
    ledger_atoms: list[Any] = field(default_factory=list)
    # 全量候选证据条目（EvidenceItem），供收缩轮次按同一预算重新组包。
    # 上方各 *_evidence 字段是本轮"模型可见"的渲染结果，本字段是其候选来源。
    evidence_items: list[Any] = field(default_factory=list)

    @property
    def merged_evidence_text(self) -> str:
        parts = [self.image_evidence.strip(), self.cross_category_evidence.strip()]
        return "\n".join(part for part in parts if part).strip()

    @property
    def has_cross_category_injection(self) -> bool:
        """Fix-08: 是否注入了跨类别证据（供 post-pipeline 决定是否预置 needs_review）。"""
        return bool(self.cross_category_evidence_sources) or bool(
            self.cross_category_evidence and self.cross_category_evidence.strip()
        )


def _normalize_enum_value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


# 卷标识 → 中文卷名(第2层: 聚合按 volume 分组, 修正原按 category 粗暴二分的卷归属错位)。
_VOLUME_LABELS: dict[str, str] = {
    "technical": "技术标",
    "price": "价格",
    "commercial": "商务标",
    "service": "服务",
    "qualification": "资格",
    "staffing": "人员",
    "performance": "业绩",
    "other": "其他",
    "unspecified": "其他",
}

# 评分项满分合计的"单一100分制"判定容差。
# 仅当满分合计接近 100 才认为适合展示统一"预估评标得分 X/Y";
# 多卷/加权/非100基准制(合计显著偏离100)机械求和不可信, 标记降级仅供参考。
_SCORE_TOTAL_TOLERANCE: float = 5.0

# 多采购包聚合: lot 缺失评分项的展示标签与"包·卷"复合键分隔符。
# 两者均为前端/报告直接展示的中文契约, 前端 resolveScopeLabel 必须保持同字面
# (由 frontend/tests/scoring-view-transforms.spec.ts 与后端聚合测试双侧锁定)。
UNASSIGNED_LOT_LABEL: str = "未识别归属"
LOT_VOLUME_SEPARATOR: str = "·"

# 卷权重和判定为"完整加权制"的容差(允许 0.98~1.02)。
_VOLUME_WEIGHT_TOLERANCE: float = 0.02


def _volume_label(volume: str | None) -> str:
    """卷标识 → 中文卷名(未知值原样回退)。"""
    key = (volume or "unspecified").strip().lower()
    return _VOLUME_LABELS.get(key, volume or "其他")


def _display_volume_label(volume: str | None, volume_label: str | None = None) -> str:
    """评分卷展示名：优先用表内原文标签，缺失时回退标准卷名。"""
    label = str(volume_label or "").strip()
    return label or _volume_label(volume)

# 从审查意见(comment)中兜底解析预估得分。
# 根因(复发): 审查输出 JSON 模板 estimated_score 默认 null, LLM 常把得分写进
# comment 却漏填结构化字段; 且不同 compliance 等级下表述差异大("预估得分:X"/"得分X分"
# /"不得分"), 单格式正则必然覆盖不全 -> 徽标(读结构化)与意见(读文本)不一致复发。
# 设计: 按"得分语义锚点"分层匹配, 显式排除"满分/扣分/页码/字数"等噪声; 配多样本回归护栏。
_ESTIMATED_FROM_COMMENT_PATTERNS = [
    # P1 显式预估得分: "预估得分：11(分)" / "预估评标得分 约 11"
    re.compile(r"预估(?:评标)?得分[约为：:\s]*(\d+(?:\.\d+)?)"),
    # P2 斜杠分式: "预估评标 8 / 满分 10" / "得分 8/15"
    re.compile(r"(?:预估(?:评标)?(?:得分)?|得分)[约为：:\s]*(\d+(?:\.\d+)?)\s*[/／]"),
    # P3 结论分式: "11分（满分15分"
    re.compile(r"(\d+(?:\.\d+)?)\s*分\s*[（(]\s*满分"),
    # P4 结论得分: "(故)得分0分" / "得分：4分" / "得分为4分"(前缀非"满", 规避"满分")
    re.compile(r"(?<!满)得分[约为：:\s]*(\d+(?:\.\d+)?)\s*分?"),
    # P5 简略得分: "综合评估得4分" / "得11分"(排除 值得/取得/获得/心得 等非评分语境)
    re.compile(r"(?<![值取获心赢])得\s*(\d+(?:\.\d+)?)\s*分"),
]

# 明确"零分"表述(无数字, 评分项不符合即不得分): "不得分/不予得分/未得分/不予计分"
_ZERO_SCORE_PATTERN = re.compile(r"(?:不予?|未)\s*得\s*分|不予计分")


def _coerce_float(value: Any) -> float | None:
    """安全转 float; None/非法返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_score(val: float, max_score: float | None) -> float:
    """将得分 clamp 到 [0, max_score] 并保留两位小数。"""
    val = max(0.0, val)
    if max_score is not None:
        try:
            val = min(val, float(max_score))
        except (TypeError, ValueError):
            pass
    return round(val, 2)


def parse_estimated_score_from_comment(
    comment: str | None,
    max_score: float | None = None,
) -> float | None:
    """从审查意见解析预估得分(评分项徽标/汇总在结构化字段缺失时的兜底)。

    覆盖 LLM 在不同 compliance 等级下的真实表述: 显式"预估得分:X"、结论式"得分X分"、
    简略"得X分"、以及无数字的"不得分"(回退 0)。显式排除"满分N分/扣N分"等噪声。

    Args:
        comment: 审查意见文本。
        max_score: 满分(用于 clamp)。

    Returns:
        解析到的预估得分(clamp 到 [0, max_score])；未解析到返回 None。
    """
    if not comment:
        return None
    for pattern in _ESTIMATED_FROM_COMMENT_PATTERNS:
        match = pattern.search(comment)
        if not match:
            continue
        val = _coerce_float(match.group(1))
        if val is None:
            continue
        return _clamp_score(val, max_score)
    # 无数值但有明确"不得分"表述 -> 0(评分项不符合即不得分)
    if _ZERO_SCORE_PATTERN.search(comment):
        return 0.0
    return None


def compute_estimated_score(
    *,
    structured_value: Any,
    comment: str | None,
    max_score: float | None,
    compliance_level: Any,
    applies: bool,
) -> float | None:
    """评分项预估得分的唯一计算口径(落库/读取/汇总共用, 杜绝多通道漂移)。

 职责: 按"LLM结构化值 > 意见文本解析 > 不符合/缺失响应回退0"的优先级产出预估得分。
 不做: 不改 compliance_level / score 既有判定; 预估仅供展示与汇总参考。

 根因复盘: 历史上落库(result_builder)、读取(review_service)、汇总三处各写
 一套兜底口径, 必然漂移(同 教训)。此函数收口为单一真源, 三处均调用。

 Args:
 structured_value: LLM 直接输出的结构化 estimated_score(可能 None / 非法)。
 comment: 审查意见文本(结构化缺失时兜底解析)。
 max_score: 满分(clamp 上界)。
 compliance_level: 符合程度(ComplianceLevel 或 str); not/missing 视为不得分。
 applies: 是否适用(评分项 scoring_item 或重点评审 is_key_review 为真)。

 Returns:
 预估得分(clamp 到 [0, max_score]); 不适用或无法判定时返回 None。
 """
    if not applies:
        return None
    val = _coerce_float(structured_value)
    if val is not None:
        return _clamp_score(val, max_score)
    parsed = parse_estimated_score_from_comment(comment, max_score)
    if parsed is not None:
        return parsed
    level = (
        compliance_level.value
        if hasattr(compliance_level, "value")
        else str(compliance_level or "")
    )
    if level.lower() in ("not", "missing"):
        return 0.0
    return None


def _scoring_field(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def is_scoring_universe_member(
    *,
    requirement_kind: Any,
    is_key_review: Any,
    max_score: Any,
) -> bool:
    """评分项满分全集判定(聚合/校验唯一口径,)。

 职责: 判定一条要求是否计入"评分项满分全集"——scoring_item 通道, 或经 
 reconcile 把评分表权威满分回填到 normal 主行的重点评审项(is_key_review 且有有效满分)。
 不做: 不改 compliance/score 判定; 不做跨行去重(同条款经 reconcile 已保证仅单行携带满分)。

 根因: reconcile 删冗余 scoring_item 并把满分搬到 normal 主行后, 聚合
 (compute_scoring_aggregate)与校验(_build_scoring_totals_report)仍只认 scoring_item,
 致回填满分被漏算(技术卷显示 60 而非 82)。收口为单一谓词供三处消费共用, 杜绝口径漂移
 (同 教训)。

 不双计前提: relevance_classifier 仅标 is_key_review 不赋 score_weight, normal 主行的
 score_weight 唯一来自 reconcile 回填(回填同时删除对应 scoring_item), 故正常流程下
 同一条款不会同时以 scoring_item 与"带满分 normal 主行"两种形态被并集计入。
 """
    if requirement_kind == "scoring_item":
        return True
    ms = _coerce_float(max_score) or 0.0
    return bool(is_key_review) and ms > 0


def _fold_volumes(
    by_volume: dict[str, dict[str, Any]], max_total: float
) -> tuple[float | None, bool]:
    """按卷加权折算到统一100分制（第2层）。

 职责: 每卷带 weight/volume_max 且权重和≈100% 时, 按"卷得分率×权重×100"合成统一总分;
 否则回退"单一100分制"判定(满分合计≈100)或降级。
 不做: 不裁剪卷内数据(调用侧已 clamp)。

 Returns:
 (unified_total, reliable):
 - unified_total 非 None: 加权折算成功的统一100分总分。
 - unified_total 为 None 且 reliable=True: 单一100分制, 用 estimated_total/max_total 展示。
 - reliable=False: 多卷/缺权重/非100制, 前端降级仅按卷参考。
 """
    if not by_volume:
        return None, False
    weighted = [
        b for b in by_volume.values()
        if b.get("weight") and float(b.get("volume_max") or 0) > 0
    ]
    weight_sum = round(sum(float(b["weight"]) for b in weighted), 4)
    covers_all_volumes = len(weighted) == len(by_volume)
    all_volumes_have_items = all(b.get("count", 0) > 0 for b in by_volume.values())
    if (
        weighted
        and covers_all_volumes
        and all_volumes_have_items
        and abs(weight_sum - 1.0) <= _VOLUME_WEIGHT_TOLERANCE
    ):
        total = 0.0
        for b in weighted:
            volume_max = float(b["volume_max"])
            rate = b["estimated"] / volume_max if volume_max > 0 else 0.0
            total += rate * float(b["weight"]) * 100.0
        return round(min(total, 100.0), 2), True
    # 回退: 单一100分制(满分合计≈100, 无加权)
    if abs(max_total - 100.0) <= _SCORE_TOTAL_TOLERANCE:
        return None, True
    return None, False


def _unique_volume_labels(items: list[Any]) -> dict[str, str]:
    """标准卷 → 唯一原文展示标签；混合新旧数据时给缺标签行继承同卷标签。"""
    labels_by_volume: dict[str, set[str]] = {}
    for it in items:
        if not is_scoring_universe_member(
            requirement_kind=_scoring_field(it, "requirement_kind"),
            is_key_review=_scoring_field(it, "is_key_review"),
            max_score=_scoring_field(it, "max_score"),
        ):
            continue
        volume = str(_scoring_field(it, "volume") or "unspecified")
        label = str(_scoring_field(it, "volume_label") or "").strip()
        if label:
            labels_by_volume.setdefault(volume, set()).add(label)
    return {
        volume: next(iter(labels))
        for volume, labels in labels_by_volume.items()
        if len(labels) == 1
    }


def _aggregate_scoring_scope(items: list[Any]) -> dict[str, Any]:
    """单一核计范围(整文档或单个采购包)内的评分项聚合。

 职责: 展示桶按条目级分组标签(volume_label)细分(: 同一粗卷可含
 "技术建议书/主要人员/业绩"多个用户可见分组, 桶键必须与明细行过滤键一致);
 加权折算仍按粗卷(volume)口径(卷权重是卷级属性, 不随展示分组拆分)。
 不做: 不感知采购包(lot)维度——多包分组由 compute_scoring_aggregate 调度。
 """
    count = 0
    estimated_count = 0
    max_total = 0.0
    by_volume: dict[str, dict[str, Any]] = {}
    fold_by_volume: dict[str, dict[str, Any]] = {}
    labels_per_volume: dict[str, set[str]] = {}
    inherited_labels = _unique_volume_labels(items)
    for it in items:
        if not is_scoring_universe_member(
            requirement_kind=_scoring_field(it, "requirement_kind"),
            is_key_review=_scoring_field(it, "is_key_review"),
            max_score=_scoring_field(it, "max_score"),
        ):
            continue
        count += 1
        try:
            max_score = float(_scoring_field(it, "max_score") or 0.0)
        except (TypeError, ValueError):
            max_score = 0.0
        volume = _scoring_field(it, "volume")
        volume_key = str(volume or "unspecified")
        own_label = str(_scoring_field(it, "volume_label") or "").strip()
        label = own_label or _display_volume_label(volume, inherited_labels.get(volume_key))
        labels_per_volume.setdefault(volume_key, set()).add(label)
        bucket = by_volume.setdefault(
            label,
            {"estimated": 0.0, "max": 0.0, "count": 0, "weight": None, "volume_max": None},
        )
        fold_bucket = fold_by_volume.setdefault(
            volume_key,
            {"estimated": 0.0, "max": 0.0, "count": 0, "weight": None, "volume_max": None},
        )
        bucket["max"] += max_score
        bucket["count"] += 1
        fold_bucket["max"] += max_score
        fold_bucket["count"] += 1
        max_total += max_score
        # 卷权重/卷满分(同卷应一致, 取首个非空; 属粗卷级, 供折算)
        if fold_bucket["weight"] is None:
            try:
                vw = _scoring_field(it, "volume_weight")
                fold_bucket["weight"] = float(vw) if vw is not None else None
            except (TypeError, ValueError):
                fold_bucket["weight"] = None
        if fold_bucket["volume_max"] is None:
            try:
                vm = _scoring_field(it, "volume_max")
                fold_bucket["volume_max"] = float(vm) if vm is not None else None
            except (TypeError, ValueError):
                fold_bucket["volume_max"] = None
        est = _scoring_field(it, "estimated_score")
        # 无分值项(满分<=0)不参与预估汇总(实测1496: 资格/格式项被赋est=100污染)。
        if est is not None and max_score > 0:
            estimated_count += 1
            try:
                est_val = float(est)
            except (TypeError, ValueError):
                est_val = 0.0
            est_val = min(max(0.0, est_val), max_score)
            bucket["estimated"] += est_val
            fold_bucket["estimated"] += est_val
    # 展示桶为粗卷 1:1 映射时回填卷权重/卷满分(拆分桶不得冒用卷级基准)
    for volume_key, labels in labels_per_volume.items():
        if len(labels) != 1:
            continue
        label = next(iter(labels))
        fold_bucket = fold_by_volume.get(volume_key) or {}
        if label in by_volume:
            by_volume[label]["weight"] = fold_bucket.get("weight")
            by_volume[label]["volume_max"] = fold_bucket.get("volume_max")
    # 卷级兜底 + 收口总预估
    estimated_total = 0.0
    for bucket in by_volume.values():
        if bucket["max"] > 0:
            bucket["estimated"] = min(bucket["estimated"], bucket["max"])
        bucket["estimated"] = round(bucket["estimated"], 2)
        bucket["max"] = round(bucket["max"], 2)
        if bucket["weight"] is not None:
            bucket["weight"] = round(float(bucket["weight"]), 4)
        estimated_total += bucket["estimated"]
    for fold_bucket in fold_by_volume.values():
        if fold_bucket["max"] > 0:
            fold_bucket["estimated"] = min(fold_bucket["estimated"], fold_bucket["max"])
    max_total = round(max_total, 2)

    unified_total, reliable = _fold_volumes(fold_by_volume, max_total)
    if unified_total is not None:
        # 加权折算成功: 统一总分对标 100, 各卷明细仍按卷原始展示
        estimated_total_display = unified_total
        max_total_display = 100.0
    else:
        estimated_total_display = round(estimated_total, 2)
        max_total_display = max_total

    return {
        "scoring_item_count": count,
        # 已产出预估得分的评分项数（0 表示该项目审查在本功能前完成，需重新审查）
        "estimated_item_count": estimated_count,
        "estimated_total_score": round(estimated_total_display, 2),
        "max_total_score": round(max_total_display, 2),
        "estimated_score_by_volume": by_volume,
        # True=单一100制或加权折算成功(可展示统一总分); False=多卷/缺权重/非100制, 前端降级
        "scoring_total_reliable": reliable,
    }


def _scoring_lot_key(item: Any) -> str | None:
    """取评分项的采购包/标段标识(空白归一为 None)。"""
    lot = _scoring_field(item, "lot")
    if lot is None:
        return None
    text = str(lot).strip()
    return text or None


def _aggregate_multi_lot(rows: list[Any], lot_keys: list[str]) -> dict[str, Any]:
    """多采购包文档的按包聚合（对齐确认页 _build_lot_totals_report_payload 语义）。

 职责: 每包独立套用单包聚合(卷分组/加权折算/可靠性判定), 产出 estimated_score_by_lot;
 lot 缺失条目归入"未识别归属"桶 fail-visible(教训: 禁止静默丢弃)。
 不做: 不跨包合成统一总分——各包独立百分制, 相加(如 100+100=200)无评标语义,
 故顶层 scoring_total_reliable 恒 False, 消费端须优先走 by_lot 分支。
 """
    by_lot: dict[str, dict[str, Any]] = {}
    merged_by_volume: dict[str, dict[str, Any]] = {}
    total_count = 0
    total_estimated_count = 0
    total_estimated = 0.0
    total_max = 0.0

    groups: list[tuple[str, list[Any]]] = [
        (lot, [r for r in rows if _scoring_lot_key(r) == lot]) for lot in lot_keys
    ]
    unassigned = [r for r in rows if _scoring_lot_key(r) is None]
    if unassigned:
        groups.append((UNASSIGNED_LOT_LABEL, unassigned))

    for label, group_rows in groups:
        sub = _aggregate_scoring_scope(group_rows)
        by_lot[label] = {
            "estimated": sub["estimated_total_score"],
            "max": sub["max_total_score"],
            "count": sub["scoring_item_count"],
            "estimated_count": sub["estimated_item_count"],
            "reliable": sub["scoring_total_reliable"],
            "unassigned": label == UNASSIGNED_LOT_LABEL,
            "by_volume": sub["estimated_score_by_volume"],
        }
        for vol_label, bucket in sub["estimated_score_by_volume"].items():
            merged_by_volume[f"{label}{LOT_VOLUME_SEPARATOR}{vol_label}"] = bucket
        total_count += sub["scoring_item_count"]
        total_estimated_count += sub["estimated_item_count"]
        total_estimated += sub["estimated_total_score"]
        total_max += sub["max_total_score"]

    return {
        "scoring_item_count": total_count,
        "estimated_item_count": total_estimated_count,
        # 跨包合计仅作数据透传(如 200 分), 无统一总分语义, 消费端禁止直接展示
        "estimated_total_score": round(total_estimated, 2),
        "max_total_score": round(total_max, 2),
        # 键为"包·卷"复合标签, 供结果页评分卷导航按包细分(与明细行 lot+volume 对齐)
        "estimated_score_by_volume": merged_by_volume,
        "scoring_total_reliable": False,
        "estimated_score_by_lot": by_lot,
    }


def compute_scoring_aggregate(items: Iterable[Any]) -> dict[str, Any]:
    """汇总评分项预估总得分（P2 闭环 + 折算 + 全集 + 按包）。

 职责: 评分项预估聚合唯一真源——识别采购包(lot)维度, ≥2 包时按包独立核计
 (estimated_score_by_lot), 单包/无包沿用按卷聚合与可靠性判定。
 不做: 不改既有 weighted_score / conclusion 口径。

 满分全集: 计入 scoring_item 与 reconcile 回填到 normal 主行的重点评审项
 (is_key_review 且 max_score>0), 由 is_scoring_universe_member 收口判定, 杜绝漏算。

 多包口径: 与确认页 build_totals_report_payload 同阈值(lot 去重后 ≥2 才
 进入分包模式), 每包内部复用单包聚合(clamp/卷级兜底/加权折算), 未归属项独立成桶
 fail-visible; 各包独立百分制不可相加, 顶层不提供统一总分。

 Args:
 items: dict 或对象序列，需含 requirement_kind / is_key_review / max_score
 / estimated_score / volume / volume_weight / volume_max / lot。

 Returns:
 聚合 dict; estimated_score_by_lot 仅多包文档非空, 其余场景为空 dict(旧行为)。
 """
    rows = [
        it for it in items
        if is_scoring_universe_member(
            requirement_kind=_scoring_field(it, "requirement_kind"),
            is_key_review=_scoring_field(it, "is_key_review"),
            max_score=_scoring_field(it, "max_score"),
        )
    ]
    lot_keys = sorted({key for key in (_scoring_lot_key(r) for r in rows) if key})
    if len(lot_keys) >= 2:
        return _aggregate_multi_lot(rows, lot_keys)
    return {**_aggregate_scoring_scope(rows), "estimated_score_by_lot": {}}


def build_review_summary(project_id: int, items: Iterable[ReviewSummaryItem]) -> dict[str, Any]:
    """计算唯一权威的审查汇总结果。"""
    rows = list(items)
    total_count = len(rows)
    if total_count == 0:
        return {
            "project_id": project_id,
            "total_count": 0,
            "compliant_count": 0,
            "non_compliant_count": 0,
            "fully_count": 0,
            "partially_count": 0,
            "not_count": 0,
            "missing_count": 0,
            "high_risk_count": 0,
            "medium_risk_count": 0,
            "low_risk_count": 0,
            "total_score": 0.0,
            "average_score": 0.0,
            "weighted_score": 0.0,
            "conclusion": "pending",
            "conclusion_reason": "暂无审查结果",
        }

    compliant_count = sum(1 for item in rows if bool(item.is_compliant))

    fully_count = sum(
        1 for item in rows
        if _normalize_enum_value(item.compliance_level) == ComplianceLevel.FULLY.value
    )
    partially_count = sum(
        1 for item in rows
        if _normalize_enum_value(item.compliance_level) == ComplianceLevel.PARTIALLY.value
    )
    not_count = sum(
        1 for item in rows
        if _normalize_enum_value(item.compliance_level) == ComplianceLevel.NOT.value
    )
    missing_count = sum(
        1 for item in rows
        if _normalize_enum_value(item.compliance_level) == ComplianceLevel.MISSING.value
    )

    high_risk_count = sum(
        1 for item in rows if _normalize_enum_value(item.risk_level) == RiskLevel.HIGH.value
    )
    medium_risk_count = sum(
        1 for item in rows if _normalize_enum_value(item.risk_level) == RiskLevel.MEDIUM.value
    )
    low_risk_count = sum(
        1 for item in rows if _normalize_enum_value(item.risk_level) == RiskLevel.LOW.value
    )

    total_score = sum(item.score for item in rows)
    average_score = total_score / total_count

    category_scores: dict[str, list[int]] = {}
    for item in rows:
        category_scores.setdefault(item.category or "unknown", []).append(item.score)

    weighted_score = 0.0
    for category, scores in category_scores.items():
        weight = CATEGORY_WEIGHTS.get(category, 0.1)
        weighted_score += weight * (sum(scores) / len(scores))

    if high_risk_count > 0:
        conclusion = "fail"
        conclusion_reason = f"存在{high_risk_count}项高风险项（否决项不符合）"
    elif weighted_score >= 80:
        conclusion = "pass"
        conclusion_reason = f"综合得分{weighted_score:.1f}分，符合要求"
    elif weighted_score >= 60:
        conclusion = "pending"
        conclusion_reason = f"综合得分{weighted_score:.1f}分，部分项目需要澄清"
    else:
        conclusion = "fail"
        conclusion_reason = f"综合得分{weighted_score:.1f}分，不符合要求"

    return {
        "project_id": project_id,
        "total_count": total_count,
        "compliant_count": compliant_count,
        "non_compliant_count": total_count - compliant_count,
        "fully_count": fully_count,
        "partially_count": partially_count,
        "not_count": not_count,
        "missing_count": missing_count,
        "high_risk_count": high_risk_count,
        "medium_risk_count": medium_risk_count,
        "low_risk_count": low_risk_count,
        "total_score": total_score,
        "average_score": average_score,
        "weighted_score": weighted_score,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
    }

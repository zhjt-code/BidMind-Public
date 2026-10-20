"""评分项卷定义行解析与剥离（第1层）。

职责: 从 LLM 提取的评分项中识别"卷定义/总分说明行"(如"技术总分100分，按40%权重折算"),
 解析卷权重(weight)与卷满分(volume_max), 并将其与真实评分子项分离。
不做: 不做 LLM 调用、不做落库、不做评分子项内容提取(scoring_extractor 职责)。

定位: 评标办法常把"卷总分+权重折算"写成独立一行(图3 表3.2.2),
 被 LLM 误提为 score_weight=100 的评分项, 既污染满分合计(总分超100),
 又丢失卷权重信息。本模块把这类行转化为"卷定义"(供按卷加权折算), 而非评分子项。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from app.core.extractor.scoring.column_role_inference import volume_from_group_text
from app.core.extractor.scoring.volume_labeling import service_volume_signal

# "技术总分100分" / "价格总分 100 分" / "总分：100分"
_VOLUME_MAX_PATTERN = re.compile(r"总分[\s:：]*(\d+(?:\.\d+)?)\s*分")
# "按40%权重进行折算" / "按 60 % 折算" / "按40%权重折算"
_VOLUME_WEIGHT_PATTERN = re.compile(
    r"按\s*(\d+(?:\.\d+)?)\s*[%％]\s*(?:权重)?\s*(?:进行)?\s*折算"
)
# 平铺"分值构成"声明: "商务技术评审部分： 20 分" / "商务分：10分" / "投标报价：30分"
_FLAT_COMPOSITION_TRIGGER_TERMS = (
    "分值构成",
    "分值分配",
    "分值组成",
    "分别进行量化评审",
    "分别进行评审",
)
_FLAT_PAIR_PATTERN = re.compile(
    # 末尾的"分"必须是分值单位, 不能是"分值/分配/分组"的首字——holdout-24 实测
    # "响应性评审标准:" 后紧跟标题 "2.2 分值构成与评分标准", 旧式会误捕成 2.2 分,
    # 伪声明再污染组对合计使真实声明对不上百分制。判据是字符切分, 非词表。
    r"([\u4e00-\u9fff]{2,12})[\s:：]*[:：]\s*(\d+(?:\.\d+)?)\s*分(?![值配组])"
)
_FLAT_LABEL_SKIP_TERMS = ("总分", "合计", "满分", "分值构成", "分值分配", "分值组成")
# 内联"总分+其中"约束句(缺口A, 样本11): "总分共100分,其中商务5分,技术50分,服务5分,价格40分"。
# 触发从严: "总分[共为]N分"与"其中"近距共现; 配对无冒号但仅扫描"其中"之后的同句段。
_INLINE_COMPOSITION_TRIGGER_RE = re.compile(
    r"总分[共为]?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*分[^。；;\n]{0,12}?其中"
)
_INLINE_PAIR_RE = re.compile(r"([\u4e00-\u9fff]{1,8}?)\s*(\d+(?:\.\d+)?)\s*分")
_INLINE_SENTENCE_END_RE = re.compile(r"[。；;\n]")
_EACH_PART_PATTERN = re.compile(
    r"分\s*([\u4e00-\u9fff、，,和及]{2,30})\s*(?:三|二|两|\d+)?个?部分"
    r".{0,80}?每个部分满分均?为?\s*(\d+(?:\.\d+)?)\s*分"
)
_COMPOSITION_SENTENCE_SPLIT_RE = re.compile(r"[。；;]\s*|\n+")
# 并列卷权重声明的定位触发: "…分别占30%、15%、55%的权重…"。
# 仅作句子定位, 真正的采纳门禁在 weighted_share_composition 内。
_WEIGHTED_SHARE_TRIGGER_RE = re.compile(r"分别占[^。；;]{0,60}?权重")
# PDF 表格按阅读序压平时，列头可能与首个组名无分隔粘连：
# "编列内容资信业绩部分:3分"。它是结构列头，不是开放集合组名的一部分。
_COMPOSITION_STRUCTURAL_PREFIXES = ("编列内容",)


@dataclass(frozen=True)
class VolumeDefinition:
    """卷定义: 卷标识 + 卷权重(归一到 0-1) + 卷满分。"""

    volume: str
    weight: float
    volume_max: float
    label: str | None = None
    # 框架表来源标记与行级证据, 供文档级缺卷补事实门禁使用。
    source: str | None = None
    evidence_text: str | None = None
    row_title: str | None = None


def declared_volumes_without_coverage(
    covered_volumes: set[str],
    declared_volume_defs: dict[str, Any] | None,
) -> list[tuple[str, float]]:
    """返回已声明卷基准(volume_max>0)但未被任何评分事实覆盖的卷及其基准。

 职责: "卷声明完备性"的单源判定——scope_validator 的闭合校验(缺口B)与
 scheme_segmentation 的方案闭合判定(伴生误切分)共用同一语义,
 防止两处各写一份后漂移。
 不做: 不判定 needs_review、不切分方案(调用方职责)。
 """
    if not declared_volume_defs:
        return []
    out: list[tuple[str, float]] = []
    for volume, vdef in declared_volume_defs.items():
        try:
            basis = float(getattr(vdef, "volume_max", 0) or 0)
        except (TypeError, ValueError):
            continue
        if basis > 0 and volume not in covered_volumes:
            out.append((volume, basis))
    return out


def parse_volume_definition(item: Any) -> Optional[VolumeDefinition]:
    """识别单个评分项是否为"卷定义/总分说明行"并解析卷权重/卷满分。

    判定(强特征, 两条件同时命中): description 含"总分N分" 且 含"按M%...折算"。
    返回 None 表示该项是普通评分子项(非卷定义)。
    """
    description = getattr(item, "description", "") or ""
    m_max = _VOLUME_MAX_PATTERN.search(description)
    m_weight = _VOLUME_WEIGHT_PATTERN.search(description)
    if not (m_max and m_weight):
        return None
    try:
        volume_max = float(m_max.group(1))
        weight_pct = float(m_weight.group(1))
    except (TypeError, ValueError):
        return None
    if volume_max <= 0 or weight_pct <= 0:
        return None
    volume = getattr(item, "volume", None) or "other"
    return VolumeDefinition(
        volume=str(volume),
        weight=round(weight_pct / 100.0, 4),
        volume_max=volume_max,
        label=_volume_label_from_key(str(volume)),
    )


def volume_defs_from_group_subtotals(subtotals: list[Any]) -> dict[str, VolumeDefinition]:
    """把评分表组小计声明升级为卷定义（最终得分制）。

    职责: 将 `商务标34分/技术标66分` 这类表内组基准转为 volume_defs。
    不做: 不扫描表格、不产评分项、不处理多卷各100分折算制。
    """
    parsed: list[tuple[str, float, str]] = []
    seen: set[str] = set()
    for subtotal in subtotals:
        label = str(getattr(subtotal, "label", "") or "").strip()
        volume = _volume_from_subtotal_label(label)
        if not volume or volume in seen:
            continue
        try:
            score = float(getattr(subtotal, "score", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if score <= 0:
            continue
        parsed.append((volume, score, label))
        seen.add(volume)
    if len(parsed) < 2:
        return {}
    total = round(sum(score for _volume, score, _label in parsed), 4)
    if abs(total - 100.0) > 1.0:
        return {}
    return {
        volume: VolumeDefinition(
            volume=volume,
            weight=round(score / total, 4),
            volume_max=score,
            label=label or _volume_label_from_key(volume),
        )
        for volume, score, label in parsed
    }


def _volume_from_subtotal_label(label: str) -> str:
    compact = str(label or "").replace(" ", "")
    if any(k in compact for k in ("报价", "价格", "评标价")):
        return "price"
    if "商务" in compact or "综合" in compact:
        return "commercial"
    if "技术" in compact:
        return "technical"
    if service_volume_signal(compact):
        return "service"
    if any(k in compact for k in ("资信", "信用", "信誉")):
        return "qualification"
    return ""


def _composition_volume(label: str) -> str | None:
    """分值构成语境专用卷别映射（止损）。

 职责: 将构成声明中的组标签映射为卷别键, 补全项目管理大纲/其他评分因素等
 volume_from_group_text 未覆盖的标签；平铺/内联/每部分满分路径共用。
 不做: 不用于表格组小计或非构成声明语境。
 """
    compact = str(label or "").replace(" ", "")
    if "项目管理大纲" in compact:
        return "technical"
    if "其他评分因素" in compact or "其他因素" in compact:
        return "other"
    mapped = volume_from_group_text(compact, "")
    return mapped or None


def _build_composition_definitions(
    pairs: list[tuple[str, float]],
    total: float,
    *,
    append_part_suffix: bool = False,
    min_defs: int = 2,
) -> dict[str, VolumeDefinition]:
    """从构成 label/pair 列表构建卷定义，未知或冲突标签 fail-closed 拒收。

    职责: 统一平铺/内联/每部分满分三条路径的映射完整性校验。
    不做: 不解析原始文本、不提取 pair。
    """
    defs: dict[str, VolumeDefinition] = {}
    unresolved_labels: list[str] = []
    for label, points in pairs:
        mapped_label = f"{label}部分" if append_part_suffix else label
        volume = _composition_volume(mapped_label)
        if volume is None:
            unresolved_labels.append(label)
            continue
        if volume in defs:
            unresolved_labels.append(label)
            continue
        defs[volume] = VolumeDefinition(
            volume=volume,
            weight=round(points / total, 4),
            volume_max=points,
            label=label,
        )
    mapped_total = round(sum(definition.volume_max for definition in defs.values()), 4)
    if unresolved_labels or len(defs) != len(pairs):
        return {}
    if abs(mapped_total - total) > 1.0:
        return {}
    return defs if len(defs) >= min_defs else {}


def _volume_label_from_key(volume: str) -> str:
    labels = {
        "technical": "技术标",
        "commercial": "商务标",
        "price": "价格",
        "service": "服务",
        "qualification": "资格",
        "staffing": "人员",
        "performance": "业绩",
    }
    return labels.get((volume or "").strip().lower(), volume)


def clean_composition_label(label: str) -> str:
    """清理分值构成配对标签中的载体边界与结构列头，不做卷别语义推断。"""
    compact = re.sub(r"\s+", "", str(label or "")).strip("|:：;；,，。")
    for prefix in _COMPOSITION_STRUCTURAL_PREFIXES:
        if compact.startswith(prefix) and len(compact) > len(prefix):
            compact = compact[len(prefix):]
            break
    return compact


def parse_flat_volume_composition(text: str) -> dict[str, VolumeDefinition]:
    """解析平铺"分值构成"声明为组基准（, 最终得分制）。

 形态: "分值构成（总分100分）: 商务技术评审部分20分; 价格评审部分80分"——各组分值
 即最终得分基准(非折算), volume_max=组分值, weight=组分值/总分。
 触发从严: 须含"分值构成"类触发词且解析出 >=2 个有效组(排除 总分/合计 标签),
 避免把普通规则文本("投标报价：30分得满分")误当组基准。
 """
    raw = str(text or "")
    # 并列卷权重声明("满分均为100…分别占30%、15%、55%")的组分值是卷内
    # 原始满分而非最终得分, 必须先于"分值构成"路径识别——后者会按 分值/总分 反推
    # 权重, 把 3 个原始 100 分横向相加成 300 分制。
    weighted_shares = _parse_weighted_share_defs(raw)
    if weighted_shares:
        return weighted_shares
    if not any(term in raw for term in _FLAT_COMPOSITION_TRIGGER_TERMS):
        return _parse_inline_composition(raw)
    each_part = _parse_each_part_composition(raw)
    if each_part:
        return each_part
    pairs: list[tuple[str, float]] = []
    for match in _FLAT_PAIR_PATTERN.finditer(raw):
        label = clean_composition_label(match.group(1))
        if any(term in label for term in _FLAT_LABEL_SKIP_TERMS):
            continue
        try:
            points = float(match.group(2))
        except (TypeError, ValueError):
            continue
        if points <= 0:
            continue
        pairs.append((label, points))
    if len(pairs) < 2:
        return {}

    declared_total = None
    m_total = _VOLUME_MAX_PATTERN.search(raw)
    if m_total:
        try:
            declared_total = float(m_total.group(1))
        except (TypeError, ValueError):
            declared_total = None
    pair_total = round(sum(p for _l, p in pairs), 4)
    if declared_total is None and abs(pair_total - 100.0) > 1.0:
        return {}
    total = declared_total if declared_total and declared_total > 0 else pair_total
    if total <= 0:
        return {}

    return _build_composition_definitions(pairs, total, min_defs=2)


def _parse_weighted_share_defs(raw: str) -> dict[str, VolumeDefinition]:
    """并列评分卷权重声明 -> 卷定义。

 与"分值构成"形态的轴差异: volume_max 取卷内**原始满分**(各卷同为 N),
 weight 取原文声明的折算比例, 二者分属不同轴, 禁止用 分值/总分 反推。
 卷别映射不完整或重复时整体拒收(fail-closed, 与 _build_composition_definitions 同纪律)。
 """
    from app.core.extractor.scoring.weighted_share_composition import (
        parse_weighted_share_composition,
    )

    shares = parse_weighted_share_composition(raw)
    if not shares:
        return {}
    defs: dict[str, VolumeDefinition] = {}
    for share in shares:
        volume = _composition_volume(share.label)
        if volume is None or volume in defs:
            return {}
        defs[volume] = VolumeDefinition(
            volume=volume,
            weight=share.weight,
            volume_max=share.max_score,
            label=share.label,
        )
    return defs


def _parse_inline_composition(raw: str) -> dict[str, VolumeDefinition]:
    """解析内联"总分共N分,其中X m分、Y n分..."约束句(缺口A, 样本11)。

    从严三闸: ①"总分[共为]N分...其中"近距共现才触发; ②配对仅扫描"其中"起至句末
    的同句段(无冒号配对易误伤, 限定语境); ③解析出>=3个可归卷组且组分和=声明总分
    (容差1)才生效, 防止普通规则文本("每高1%扣0.6分")被误当组基准。
    """
    trigger = _INLINE_COMPOSITION_TRIGGER_RE.search(raw)
    if not trigger:
        return {}
    try:
        declared_total = float(trigger.group(1))
    except (TypeError, ValueError):
        return {}
    if declared_total <= 0:
        return {}
    tail = raw[trigger.end():]
    sentence_end = _INLINE_SENTENCE_END_RE.search(tail)
    segment = tail[: sentence_end.start()] if sentence_end else tail
    pairs: list[tuple[str, float]] = []
    for match in _INLINE_PAIR_RE.finditer(segment):
        label = clean_composition_label(match.group(1).strip("、，, "))
        if not label or any(term in label for term in _FLAT_LABEL_SKIP_TERMS):
            continue
        try:
            points = float(match.group(2))
        except (TypeError, ValueError):
            continue
        if points <= 0:
            continue
        pairs.append((label, points))
    if len(pairs) < 3:
        return {}
    if abs(round(sum(p for _l, p in pairs), 4) - declared_total) > 1.0:
        return {}
    return _build_composition_definitions(
        pairs, declared_total, append_part_suffix=True, min_defs=3
    )


def extract_volume_composition_sentence(text: str) -> str | None:
    """从段落中定位可解析的卷定义声明句（供 scoring_chunk_builder 注入 prelude）。

    职责: 只定位声明句, 不解析/改写卷值; 解析仍由 parse_flat_volume_composition 统一完成。
    不做: 不扫描表格、不做评分项提取、不调用 LLM/DB。
    """
    raw = str(text or "")
    if not raw:
        return None
    # 句子可能被换行拆散, 用相邻窗口拼回再交给 parse_flat_volume_composition 验证。
    parts = [p.strip() for p in _COMPOSITION_SENTENCE_SPLIT_RE.split(raw) if p.strip()]
    for idx in range(len(parts)):
        window = " ".join(parts[idx : idx + 2])
        has_trigger = (
            any(term in window for term in _FLAT_COMPOSITION_TRIGGER_TERMS)
            or _INLINE_COMPOSITION_TRIGGER_RE.search(window) is not None
            # 并列卷权重声明无"分值构成"字样, 漏加触发词则该句永不被定位,
            # prelude 不注入, 账本与卷权重双双拿不到基准。
            or _WEIGHTED_SHARE_TRIGGER_RE.search(window) is not None
        )
        if not has_trigger:
            continue
        if parse_flat_volume_composition(window):
            return window
    return None


def _parse_each_part_composition(raw: str) -> dict[str, VolumeDefinition]:
    """解析“分技术、商务、价格三个部分...每个部分满分均为100分”句式。"""
    match = _EACH_PART_PATTERN.search(raw)
    if not match:
        return {}
    labels = [
        token.strip()
        for token in re.split(r"[、，,和及]+", match.group(1))
        if token.strip()
    ]
    if len(labels) < 2:
        return {}
    try:
        each_score = float(match.group(2))
    except (TypeError, ValueError):
        return {}
    if each_score <= 0:
        return {}
    pairs = [(label, each_score) for label in labels]
    total = each_score * len(labels)
    defs = _build_composition_definitions(pairs, total, min_defs=2)
    if len(defs) < 2:
        return defs
    keys = list(defs.keys())
    weight_acc = round(sum(defs[key].weight for key in keys[:-1]), 4)
    last_key = keys[-1]
    last = defs[last_key]
    defs[last_key] = VolumeDefinition(
        volume=last.volume,
        weight=round(1.0 - weight_acc, 4),
        volume_max=last.volume_max,
        label=last.label,
    )
    return defs


def split_definitions_and_items(
    items: list,
) -> tuple[dict[str, VolumeDefinition], list]:
    """分离"卷定义行"与真实评分子项。

    Args:
        items: ExtractedScoringItem 序列(已去重)。

    Returns:
        (volume_defs, scoring_items)
        - volume_defs: 按 volume 聚合的卷定义(同卷多行取首个)
        - scoring_items: 剔除卷定义行后的真实评分子项
    """
    volume_defs: dict[str, VolumeDefinition] = {}
    scoring_items: list = []
    for it in items:
        vdef = parse_volume_definition(it)
        if vdef is not None:
            volume_defs.setdefault(vdef.volume, vdef)
            continue
        scoring_items.append(it)
    return volume_defs, scoring_items


# 价格卷标识(按报价公式计分, 通常无评分子项; 不作为孤儿卷归并目标)。
_PRICE_VOLUMES = {"price"}


def _orphan_remap_target(volume_defs: dict[str, VolumeDefinition]) -> Optional[str]:
    """孤儿卷(volume 不在 volume_defs)的唯一归并目标 = 唯一的"非价格定义卷"。

 仅在"框架同时定义了价格卷 + 唯一非价格卷"(即经典"技术综合+价格公式"加权制)时归并;
 否则(无价格卷 / 多个非价格卷)返回 None 不归并, 保持降级——避免在非加权制场景误并。

 依据(同源 B 卷归属): 加权制常仅"技术综合评审卷 + 价格公式卷", 其内部子段
 (商务/资信/维保)被 scoring_table_parser._infer_table_volume 按 section 文案误判为独立卷
 (如 commercial), 实属技术综合卷。孤儿卷不在框架定义内 → 无 weight/volume_max → 拖垮
 _fold_volumes 加权折算且错卷展示。该结构下唯一非价格卷时归并之, 既治错卷又复原满分归属。
 """
    has_price = any(v in _PRICE_VOLUMES for v in volume_defs)
    non_price = [v for v in volume_defs if v not in _PRICE_VOLUMES]
    if has_price and len(non_price) == 1:
        return non_price[0]
    return None


def apply_volume_definitions(
    items: list, volume_defs: dict[str, VolumeDefinition]
) -> None:
    """把卷定义的 weight/volume_max 就地回填到对应 volume 的每个评分子项。

 仅回填子项自身未携带(为 None)的字段, 不覆盖 LLM 已提取的卷权重。
 孤儿卷子项(volume 非空但不在 volume_defs)在框架内存在唯一非价格定义卷时归并到该卷
 (B), 否则保持原样(聚合时该卷视为无权重而降级)。
 """
    remap_target = _orphan_remap_target(volume_defs)
    for it in items:
        volume = getattr(it, "volume", None)
        # 孤儿卷归并: volume 非空但框架未定义, 且存在唯一非价格定义卷 → 归并(防错卷+拖垮折算)
        if volume and volume not in volume_defs and remap_target is not None:
            try:
                it.volume = remap_target
                volume = remap_target
            except (AttributeError, ValueError):
                pass
        if not volume or volume not in volume_defs:
            continue
        vdef = volume_defs[volume]
        if getattr(it, "volume_weight", None) is None:
            try:
                it.volume_weight = vdef.weight
            except (AttributeError, ValueError):
                pass
        if getattr(it, "volume_max", None) is None:
            try:
                it.volume_max = vdef.volume_max
            except (AttributeError, ValueError):
                pass
        if getattr(it, "volume_label", None) is None and getattr(vdef, "label", None):
            try:
                it.volume_label = vdef.label
            except (AttributeError, ValueError):
                pass

"""VLM 候选契约: JSON Lines 解析、净化去重、映射到 ExtractedScoringItem。

职责: 把 VLM 的行式输出解析为草稿项, 执行生产同口径后处理(命名净化 +
 红线一确定性去重), 并映射为既有 ExtractedScoringItem 契约。
不做: 不调用 LLM、不做锚定/闭合门禁、不落库。

后处理规则移植自 MVP 三轮迭代(scripts/evaluate_scoring_vlm_mvp.py), 两条
已锁定教训: ①group_path 自引用不构成祖先证据(实测全项互相毁灭);
②精确去重身份必须含规则文本, 否则同表对称因素被误删。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

_STANDARD_ROW_MARKERS = ("评分标准", "计算公式", "得分计算", "评分办法")
_COMPOSITION_TABLE_MARKERS = ("分值构成", "评分构成", "分值组成", "评分分值")
_LOT_PATTERN = re.compile(r"(采购包|标段|分包)\s*[0-9一二三四五六七八九十]+|^包\s*[0-9一二三四五六七八九十]+")


# ════════════════════════════════════════════════════════════════════════════
# 行式解析
# ════════════════════════════════════════════════════════════════════════════


def _coerce_float(value) -> Optional[float]:
    """把 '10分'、'0-5' 等宽松写法纠偏为数值(取末尾数字), 无法解析返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    matches = re.findall(r"\d+(?:\.\d+)?", str(value))
    return float(matches[-1]) if matches else None


def _balanced_top_level_objects(text: str) -> list[str]:
    """花括号配平扫描顶层 JSON 对象块(字符串内花括号不计深度)。

    宽容回退专用: 兼容整段美化(多行)对象与数组包装; 未闭合块自然丢弃。
    """
    blocks: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escaped = False
    for index, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth > 0:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = index
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                blocks.append(text[start : index + 1])
                start = -1
    return blocks


def extract_jsonl_records(text: str) -> tuple[Optional[dict], list[dict], int]:
    """解析 JSON Lines 响应, 返回 (header, item_dicts, broken_lines)。

    截断韧性: 响应尾部被 max_tokens 腰斩时已完整输出的行仍全部可解析,
    只损失尾行(计入 broken_lines)。容忍思考段、markdown 围栏与行尾逗号。
    行式零命中时退化为花括号配平扫描(pdf_direct 裁决: 模型输出美化JSON/
    数组包装不再整段判零项, 3479 实测 1.6万字回复曾因此作废)。
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    cleaned = re.sub(r"```(?:json)?", "\n", cleaned)
    header: Optional[dict] = None
    items: list[dict] = []
    broken = 0

    def _classify(record: Any) -> None:
        nonlocal header
        if not isinstance(record, dict):
            return
        if record.get("score_item_name"):
            items.append(record)
        elif header is None and (
            record.get("type") == "header"
            or "declared_total" in record
            or "volume_totals" in record
        ):
            header = record

    for raw_line in cleaned.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            broken += 1
            continue
        _classify(record)
    if not items:
        for block in _balanced_top_level_objects(cleaned):
            if "\n" not in block:
                continue  # 单行块已被行式路径处理过, 不重复计数
            try:
                record = json.loads(block)
            except json.JSONDecodeError:
                broken += 1
                continue
            _classify(record)
    return header, items, broken


class VlmScoringItemDraft(BaseModel):
    """VLM 单行评分细项草稿(宽松纠偏)。"""

    score_item_name: str = Field(..., min_length=1)
    score_weight: float = Field(default=0.0, ge=0)
    group_path: list[str] = Field(default_factory=list)
    fact_type: Literal["base_score", "bonus", "penalty", "price_formula"] = "base_score"
    score_rules: list[str] = Field(default_factory=list)
    source_page: Optional[int] = None
    # 3484 教训: 多方案(多详评模板)文档的方案轴必须有承接字段, 否则跨方案
    # 求和永不闭合; 单方案文档模型省略该字段。
    scheme: Optional[str] = None

    @field_validator("scheme", mode="before")
    @classmethod
    def coerce_scheme(cls, v):
        text = str(v or "").strip()
        return text or None

    @field_validator("score_weight", mode="before")
    @classmethod
    def coerce_weight(cls, v):
        value = _coerce_float(v)
        return 0.0 if value is None or value < 0 else value

    @field_validator("fact_type", mode="before")
    @classmethod
    def coerce_fact_type(cls, v):
        valid = {"base_score", "bonus", "penalty", "price_formula"}
        return v if v in valid else "base_score"

    @field_validator("score_rules", mode="before")
    @classmethod
    def coerce_rules(cls, v):
        if isinstance(v, str):
            return [v]
        return v if isinstance(v, list) else []

    @field_validator("group_path", mode="before")
    @classmethod
    def coerce_group_path(cls, v):
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, list):
            return [str(part).strip() for part in v if str(part).strip()]
        return []

    @field_validator("source_page", mode="before")
    @classmethod
    def coerce_page(cls, v):
        value = _coerce_float(v)
        return int(value) if value and value > 0 else None


def parse_item_drafts(raw_items: list[dict]) -> tuple[list[VlmScoringItemDraft], int]:
    """逐行构造草稿项, 畸形行跳过并计数(单行畸形不拖垮整批)。"""
    drafts: list[VlmScoringItemDraft] = []
    invalid = 0
    for entry in raw_items:
        try:
            drafts.append(VlmScoringItemDraft(**entry))
        except Exception:  # noqa: BLE001
            invalid += 1
    return drafts, invalid


# ════════════════════════════════════════════════════════════════════════════
# 生产同口径后处理(红线一: 不重复计分)
# ════════════════════════════════════════════════════════════════════════════


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def normalize_vlm_items(drafts: list[VlmScoringItemDraft]) -> list[VlmScoringItemDraft]:
    """命名净化 + 0分base过滤 + 父组行/标准行/精确重复/构成表副本去重。"""
    from app.core.extractor.scoring.scoring_item_naming import (
        strip_scoring_name_decoration,
    )

    kept: list[VlmScoringItemDraft] = []
    for draft in drafts:
        if draft.fact_type == "base_score" and draft.score_weight <= 0:
            continue
        cleaned_name = strip_scoring_name_decoration(draft.score_item_name)
        if not cleaned_name:
            continue
        kept.append(draft.model_copy(update={"score_item_name": cleaned_name}))

    # 父组行: 某项净化名出现在其他项的 group_path 中 → 分值已由子项承载。
    # 自引用(模型把项自身名写进自己的路径末位)不构成祖先证据。
    ancestor_names: set[str] = set()
    for draft in kept:
        own = _compact(draft.score_item_name)
        for part in draft.group_path:
            cleaned_part = _compact(strip_scoring_name_decoration(part))
            if cleaned_part and cleaned_part != own:
                ancestor_names.add(cleaned_part)
    kept = [d for d in kept if _compact(d.score_item_name) not in ancestor_names]

    # 标准行: 同分值且名称为另一项扩展前缀并含标准行标记词 → 说明行复述。
    result: list[VlmScoringItemDraft] = []
    for draft in kept:
        name = _compact(draft.score_item_name)
        is_dup = any(
            other is not draft
            and name != _compact(other.score_item_name)
            and name.startswith(_compact(other.score_item_name))
            and draft.score_weight == other.score_weight
            and any(marker in name for marker in _STANDARD_ROW_MARKERS)
            for other in kept
        )
        if not is_dup:
            result.append(draft)

    # 精确重复: (净化名, 分值, fact_type, 路径, 规则文本)全同只留首个;
    # 规则文本参与身份防误删同表对称因素。
    seen: set[tuple] = set()
    deduped: list[VlmScoringItemDraft] = []
    for draft in result:
        identity = (
            _compact(draft.score_item_name),
            draft.score_weight,
            draft.fact_type,
            tuple(_compact(p) for p in draft.group_path),
            tuple(_compact(r) for r in draft.score_rules),
        )
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(draft)

    # 构成表副本: 路径含"分值构成"类标记且明细中存在同名同分项 → 删汇总层副本。
    name_score_paths: dict[tuple, set[tuple]] = {}
    for draft in deduped:
        key = (_compact(draft.score_item_name), draft.score_weight)
        name_score_paths.setdefault(key, set()).add(
            tuple(_compact(p) for p in draft.group_path)
        )
    final: list[VlmScoringItemDraft] = []
    for draft in deduped:
        in_composition = any(
            marker in part
            for part in draft.group_path
            for marker in _COMPOSITION_TABLE_MARKERS
        )
        if in_composition:
            key = (_compact(draft.score_item_name), draft.score_weight)
            if len(name_score_paths.get(key, set())) >= 2:
                continue
        final.append(draft)
    return final


# ════════════════════════════════════════════════════════════════════════════
# 映射到既有契约
# ════════════════════════════════════════════════════════════════════════════


_VOLUME_KEYWORDS = ("价格", "报价", "商务", "综合", "技术", "服务", "资信")


def _attribute_volume(draft: VlmScoringItemDraft, volume_defs: dict) -> Optional[str]:
    """按声明卷定义归属草稿项的卷枚举; 无可靠证据返回 None(诚实留空)。

 优先级: ①卷定义 label 与 group_path 元素互为子串; ②路径/名称含卷关键词时
 经 infer_initial_table_volume 推断——仅当推断结果确在声明卷中才采纳,
 防该函数的 technical 默认值造成错卷(错卷等效静默漏提, 教训)。
 volume_defs 为空时只认 group_path 显式短标签(经济标/技术标/资信标),
 仍不走 technical 默认。
 """
    path_parts = [_compact(part) for part in draft.group_path if _compact(part)]
    if not volume_defs:
        from app.core.extractor.scoring.volume_labeling import (
            volume_from_explicit_bid_label,
        )

        for part in reversed(path_parts):
            hit = volume_from_explicit_bid_label(part)
            if hit:
                return hit[0]
        return None
    for volume, vdef in volume_defs.items():
        label = _compact(str(getattr(vdef, "label", "") or ""))
        if not label:
            continue
        for part in path_parts:
            if label in part or part in label:
                return volume

    from app.core.extractor.scoring.volume_labeling import infer_initial_table_volume

    haystack = "".join(path_parts)
    if any(keyword in haystack for keyword in _VOLUME_KEYWORDS):
        inferred = infer_initial_table_volume(haystack)
        if inferred in volume_defs:
            return inferred
    return None


def to_extracted_items(
    drafts: list[VlmScoringItemDraft], volume_defs: Optional[dict] = None
) -> list:
    """草稿项 → ExtractedScoringItem(复用既有分类推断/卷归属与字段语义)。"""
    from app.core.extractor.scoring.category_inference import infer_scoring_category
    from app.schemas.requirement import ExtractedScoringItem

    items = []
    for draft in drafts:
        rules_text = "；".join(draft.score_rules).strip()
        description = rules_text or draft.score_item_name
        if len(description) < 2:
            description = f"{draft.score_item_name}（评分项）"
        category, basis = infer_scoring_category(
            draft.score_item_name + " " + rules_text
        )
        lot = next(
            (part for part in draft.group_path if _LOT_PATTERN.search(part)), None
        )
        volume_label = draft.group_path[-1] if draft.group_path else None
        items.append(
            ExtractedScoringItem(
                category=category,
                category_basis=(f"vlm:{basis}" if basis else "llm"),
                score_item_name=draft.score_item_name,
                description=description,
                score_weight=draft.score_weight,
                volume=_attribute_volume(draft, volume_defs or {}),
                volume_label=volume_label,
                lot=lot,
                scheme=draft.scheme,
                fact_type=draft.fact_type,
                score_rules=list(draft.score_rules),
                deduction_rules=[],
                source_pages=[draft.source_page] if draft.source_page else [],
            )
        )
    return items


__all__ = [
    "VlmScoringItemDraft",
    "extract_jsonl_records",
    "normalize_vlm_items",
    "parse_item_drafts",
    "to_extracted_items",
]

"""独立短分值列评分表解析器。

职责: 基于独立短分值列从表格行中解析真实评分子项，并剥离分组小计行。
不做: 不定位评分区段、不解析嵌入式括号分值、不做DB落库或LLM兜底;
 列角色分布推断收口在 column_role_inference(与装配器共用)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.extractor.scoring.category_inference import infer_scoring_category
from app.core.extractor.scoring.scoring_item_naming import strip_scoring_name_decoration
from app.core.extractor.scoring.column_role_inference import (
    infer_column_roles,
    volume_from_group_text,
)
from app.core.extractor.scoring.score_column_semantics import (
    classify_adjustment_fact_type,
    is_total_row,
    normalize_cell,
    parse_adjustment_score,
    parse_short_score_tolerant,
)
from app.core.extractor.scoring.volume_labeling import (
    infer_scoring_group_label_from_row,
    refine_scoring_group_label,
)
from app.schemas.requirement import ExtractedScoringItem


_NUMERIC_SKIP_NAME_TERMS = (
    "分值构成",
    "分值分配",
    "分值组成",
    "总分",
    "评标基准价",
    "评分因素",
    "评分标准",
    "评审因素",
    "评审标准",
    "合计",
    "得分合计",
)
_SUBTOTAL_PART_TERMS = ("部分", "评审", "评分")
_SUBTOTAL_PREFIX_RE = re.compile(r"^[一二三四五六七八九十]+[、.．]")


@dataclass(frozen=True)
class NumericScoreColumnResult:
    """独立分值列解析结果。"""

    items: list[ExtractedScoringItem] = field(default_factory=list)
    note: str = ""
    diagnostics: list[str] = field(default_factory=list)


def parse_numeric_score_column_table(
    grid: list[list[str]], volume_default: str
) -> NumericScoreColumnResult | None:
    """按列统计逐行识别评分项。

 适用: 表内有一列大多数行是独立短分值(分值列), 但表头列名与数据列错位、
 前导小表干扰, 或同表中存在价格公式导致嵌入式路径只能局部命中。
 """
    if len(grid) < 3:
        return None
    roles = infer_column_roles(grid)
    if roles is None:
        return None

    items: list[ExtractedScoringItem] = []
    diagnostics: list[str] = []
    current_volume = volume_default
    current_volume_label: str | None = None
    volume_locked_by_group = False
    last_group = ""
    for row in grid:
        scoring_group_label = infer_scoring_group_label_from_row(row)
        if scoring_group_label is not None:
            last_group = scoring_group_label
            current_volume_label = scoring_group_label
            volume_locked_by_group = False
            continue
        if is_total_row(row):
            continue
        group_val = normalize_cell(_cell(row, roles.group)) if roles.group is not None else ""
        if group_val:
            last_group = group_val
            group_volume = volume_from_group_text(last_group, "")
            volume_locked_by_group = bool(group_volume)
            if group_volume:
                current_volume = group_volume
                current_volume_label = _clean_group_label(last_group)
        # 分值格可能被页面水印字污染("20分 照"), 用噪声容忍解析防静默丢行
        score_cell = _cell(row, roles.score)
        score = parse_short_score_tolerant(score_cell)
        name = normalize_cell(_cell(row, roles.name))
        if score is None or not name:
            # 分值恒0/负分的调整项(不良行为处理、失信分)是评审事实,
            # 不得静默丢弃; 建模为 penalty/base_score, 不进入基础满分构成。
            adjustment = parse_adjustment_score(score_cell) if name else None
            if adjustment is not None:
                adj_rule = _cell(row, roles.rule)
                adj_name = strip_scoring_name_decoration(name)
                adj_category, adj_basis = infer_scoring_category(
                    f"{last_group}{adj_name}{adj_rule}"
                )
                # 负分表达约定(schema 限定 score_weight>=0): 归零 + 扣分幅度记 deduction_rules
                adj_item = _build_item(
                    category=adj_category,
                    category_basis=adj_basis,
                    name=adj_name,
                    description=" ".join(p for p in (adj_name, adj_rule) if p).strip(),
                    score_weight=max(adjustment, 0.0),
                    volume=current_volume,
                    volume_label=refine_scoring_group_label(current_volume_label, adj_name),
                    score_rules=[adj_rule] if adj_rule and adjustment >= 0 else [],
                    deduction_rules=(
                        ([adj_rule] if adj_rule else [f"最高扣{abs(adjustment):g}分"])
                        if adjustment < 0
                        else []
                    ),
                    fact_type=classify_adjustment_fact_type(
                        adjustment, f"{adj_name}{adj_rule}"
                    ),
                )
                if adj_item is not None:
                    items.append(adj_item)
                    continue
            if score is None and name and _looks_like_failed_score_cell(score_cell):
                diagnostics.append(_format_failed_score_row(name, score_cell))
            continue

        rule = _cell(row, roles.rule)
        if is_score_subtotal_row(name, rule):
            last_group = name
            group_volume = volume_from_group_text(name, "")
            volume_locked_by_group = bool(group_volume)
            if group_volume:
                current_volume = group_volume
                current_volume_label = _clean_group_label(last_group)
            continue

        # 缺口A(样本11): 跳过词只对"名称格自身"一票否决; 行头拼接含跳过词(如合并
        # 表头格"详细评审标准"与首个明细行同行)时, 仅在该行无评分规则文本时才跳过,
        # 防止把携带组表头格的真实评分行(响应文件编写质量5分)整行误杀。
        if any(term in normalize_cell(name) for term in _NUMERIC_SKIP_NAME_TERMS):
            continue
        row_head = "".join(
            normalize_cell(_cell(row, i)) for i in range(min(len(row), (roles.name or 0) + 1))
        )
        if not normalize_cell(rule) and any(
            term in row_head for term in _NUMERIC_SKIP_NAME_TERMS
        ):
            continue

        volume = (
            current_volume
            if volume_locked_by_group
            else volume_from_group_text(f"{last_group}{name}", current_volume)
        )
        if volume == "price" and len(name) > 30:
            price_text = f"{name}{rule}"
            if "有效响应报价" in price_text:
                name = "有效响应报价"
            elif current_volume_label and "价格部分" in current_volume_label and score == 20.0:
                name = "价格"
            elif "报价评分" in price_text or ("投标报价" in price_text and "报价" in price_text):
                name = "报价评分"
            elif "评标价" in price_text:
                name = "评标价"
            elif current_volume_label:
                name = current_volume_label.replace("部分", "").replace("评审", "")
            else:
                name = "价格"
        if volume == "price" and name == "价格" and "有效响应报价" in f"{name}{rule}":
            name = "有效响应报价"
        category, category_basis = infer_scoring_category(f"{last_group}{name}{rule}")
        # 名称装饰剥离(单一权威源): 列统计路径同样受序号前缀/尾部括号分值污染
        name = strip_scoring_name_decoration(name)
        item = _build_item(
            category=category,
            category_basis=category_basis,
            name=name,
            description=" ".join(part for part in (name, rule) if part).strip(),
            score_weight=score,
            volume=volume,
            # 标签只取分组上下文, 缺失时留 None 交给卷定义回填,
            # 禁止按项名关键词造标签(refine 内含 None 守卫, 仅细分"其他因素")。
            volume_label=refine_scoring_group_label(current_volume_label, name),
            score_rules=[rule] if rule else [],
        )
        if item is not None:
            items.append(item)
    return NumericScoreColumnResult(
        items=items,
        note=f"独立分值列(列统计) {len(items)} 项",
        diagnostics=diagnostics,
    )


def is_score_subtotal_row(name: str, rule: str) -> bool:
    """识别分组小计行，只继承卷别不产评分项。

    例: `一、价格部分 | | 20`、`三、技术部分 | | 67` 是分组小计;
    `商务报价部分 | 价格公式... | 40` 有实质评分规则, 不是小计。
    """
    compact_name = normalize_cell(name)
    compact_rule = normalize_cell(rule)
    if compact_rule:
        return False
    return bool(_SUBTOTAL_PREFIX_RE.match(compact_name)) or any(
        term in compact_name for term in _SUBTOTAL_PART_TERMS
    )


def _build_item(
    *,
    category: str,
    name: str,
    description: str,
    score_weight: float,
    volume: str,
    volume_label: str | None = None,
    score_rules: list[str] | None = None,
    category_basis: str | None = None,
    fact_type: str = "base_score",
    deduction_rules: list[str] | None = None,
) -> ExtractedScoringItem | None:
    """构造 ExtractedScoringItem, 校验失败返回 None(不阻断整表)。"""
    try:
        return ExtractedScoringItem(
            category=category,
            category_basis=category_basis,
            score_item_name=(name or "评分项")[:80],
            description=(description if len(description) >= 2 else f"{description}评分项"),
            score_weight=score_weight,
            volume=volume,
            volume_label=volume_label,
            score_rules=list(score_rules or []),
            deduction_rules=list(deduction_rules or []),
            fact_type=fact_type,
        )
    except ValidationError:
        return None


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _clean_group_label(text: str) -> str:
    """分组格标签清理: 剥序号前缀与尾部分值(含括注形态"商务部分（16分）",)。"""
    compact = normalize_cell(text)
    compact = re.sub(r"^[一二三四五六七八九十]+[、.．]", "", compact)
    match = re.search(r"(.+?(?:标|部分|卷))[（(]?\d+(?:\.\d+)?分[）)]?$", compact)
    if match:
        compact = match.group(1)
    return compact


def _looks_like_failed_score_cell(text: str) -> bool:
    """分值格含数字但未能解析, 作为 fail-visible 诊断候选。"""
    raw = text or ""
    if "%" in raw or "％" in raw:
        return False
    return bool(re.search(r"\d", raw))


def _format_failed_score_row(name: str, score_cell: str) -> str:
    return f"疑似评分行分值未识别: {name} | 分值格={score_cell}"


__all__ = [
    "NumericScoreColumnResult",
    "is_score_subtotal_row",
    "parse_numeric_score_column_table",
]

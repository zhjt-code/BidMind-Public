"""评分表内部结构探测器。

职责: 基于表格内部的对象列、分值列、规则列和负向模板信号判断是否为评分相关表。
不做: 不提取评分项、不做跨页拼接、不调用 LLM/DB。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.extractor.scoring.score_column_semantics import (
    SCORE_HEADER_TERMS,
    find_embedded_scores,
    has_scoring_rule_semantics,
    is_non_score_header,
    is_ordinal_column,
    is_total_row,
    normalize_cell,
    parse_short_score,
    parse_weight_value,
    row_text,
)

_ITEM_HEADER_TERMS = (
    "评分项目",
    "评分项",
    "评分因素",
    "评审项目",
    "评审因素",
    "评分对象",
    "细分项",
    # "评议"措辞变体(详细评审/综合评分混排表常用)
    "评议内容",
    "评议因素",
)
_RULE_HEADER_TERMS = (
    "评分标准",
    "评审标准",
    "评分内容",
    "评审内容",
    "主要内容",
    "评议标准",
)
_DIRECTORY_TERMS = ("投标文件目录", "对应页码", "编制说明")
_RESULT_ONLY_TERMS = ("投标人名称及评审结论", "评审结论")
_ASSESSMENT_FORM_TERMS = ("考核项目", "扣分事由", "考核人")
_BID_EVAL_ANCHORS = ("评标", "投标人", "评审因素", "商务得分", "技术得分", "评标基准价")
BID_EVAL_ANCHORS = _BID_EVAL_ANCHORS
_OPERATIONAL_ASSESSMENT_CONTEXT = ("考核方", "被考核方", "考核时间", "考核周期")
_RULE_SIGNAL_TERMS = (
    "得满分",
    "满分",
    "每项",
    "扣",
    "不得分",
    "综合评审",
    "好",
    "较好",
    "一般",
    "无该项内容",
    "基础分",
)
_VOLUME_DEF_RE = re.compile(r"总分\s*\d+(?:\.\d+)?\s*分.*?按\s*\d+(?:\.\d+)?\s*[%％].*?折算")


@dataclass(frozen=True)
class ScoringTableAnalysis:
    """评分表结构探测结果。"""

    is_scoring_candidate: bool
    is_directory_template: bool = False
    score_columns: list[int] = field(default_factory=list)
    item_columns: list[int] = field(default_factory=list)
    rule_columns: list[int] = field(default_factory=list)
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)


def analyze_scoring_table_rows(rows: list[list[str]]) -> ScoringTableAnalysis:
    """分析二维表是否具备评分表内部结构。"""
    if not rows:
        return ScoringTableAnalysis(False, reasons=["empty"])

    width = max((len(row) for row in rows), default=0)
    if width < 2:
        return ScoringTableAnalysis(False, reasons=["too_few_columns"])

    table_text = "\n".join(row_text(row) for row in rows)
    directory_hits = sum(1 for term in _DIRECTORY_TERMS if term in table_text)
    if directory_hits >= 2:
        return ScoringTableAnalysis(
            False,
            is_directory_template=True,
            confidence=0.95,
            reasons=["directory_template"],
        )
    if _looks_like_assessment_form(table_text):
        return ScoringTableAnalysis(
            False,
            confidence=0.95,
            reasons=["assessment_form"],
        )

    score_columns = _find_score_columns(rows, width)
    item_columns = _find_header_columns(rows, _ITEM_HEADER_TERMS, width)
    rule_columns = _find_header_columns(rows, _RULE_HEADER_TERMS, width)
    # item 信号不得只依赖表头词表——混排评分表(真子表头在表中部)或非常规措辞
    # 会导致词表全 miss。"score 列有短分值 + 同行左侧有短中文对象名"是独立于词表的行级结构信号。
    structural_item_rows = _count_structural_item_rows(rows, score_columns)

    rule_signal_rows = 0
    score_signal_rows = 0
    item_signal_rows = 0
    embedded_score_rows = 0
    for row in rows:
        if is_total_row(row):
            continue
        text = row_text(row)
        if any(parse_short_score(_cell(row, idx)) is not None for idx in score_columns):
            score_signal_rows += 1
        if any(_has_chinese_short_text(_cell(row, idx)) for idx in item_columns):
            item_signal_rows += 1
        if any(term in text for term in _RULE_SIGNAL_TERMS):
            rule_signal_rows += 1
        if any(find_embedded_scores(cell) for cell in row):
            embedded_score_rows += 1

    reasons: list[str] = []
    confidence = 0.0
    if score_columns:
        confidence += 0.35
        reasons.append("score_column")
    if item_columns:
        confidence += 0.25
        reasons.append("item_column")
    elif structural_item_rows >= 1:
        confidence += 0.2
        reasons.append("item_structure")
    if rule_columns or rule_signal_rows:
        confidence += 0.25
        reasons.append("rule_signal")
    if _VOLUME_DEF_RE.search(table_text):
        confidence += 0.35
        reasons.append("volume_definition")
    if _looks_like_weight_summary(rows):
        confidence += 0.35
        reasons.append("weight_summary")
    # 无独立满分列但分值以 `（N分）` 嵌在评审因素单元格的层级评分表,
    # 是有效评分表的另一种形态(评分对象+具体分值+评分标准三元俱全), 必须识别为候选。
    is_embedded_table = embedded_score_rows >= 2
    if is_embedded_table:
        confidence += 0.35
        reasons.append("embedded_score")

    result_only = (
        any(term in table_text for term in _RESULT_ONLY_TERMS)
        and not score_columns
        and not is_embedded_table
        and rule_signal_rows == 0
    )
    if result_only:
        reasons.append("result_only")

    is_detail_table = bool(
        score_columns
        and (item_columns or item_signal_rows >= 1 or structural_item_rows >= 1)
        and (rule_columns or rule_signal_rows >= 1)
    )
    is_framework_table = bool(_VOLUME_DEF_RE.search(table_text) or _looks_like_weight_summary(rows))
    is_candidate = (is_detail_table or is_framework_table or is_embedded_table) and not result_only

    return ScoringTableAnalysis(
        is_scoring_candidate=is_candidate,
        score_columns=score_columns,
        item_columns=item_columns,
        rule_columns=rule_columns,
        confidence=round(min(confidence, 0.99), 3),
        reasons=reasons or ["no_scoring_structure"],
    )


def _find_header_columns(
    rows: list[list[str]], terms: tuple[str, ...], width: int
) -> list[int]:
    columns: list[int] = []
    for row in rows[:4]:
        for idx in range(width):
            cell = normalize_cell(_cell(row, idx))
            if any(term in cell for term in terms) and idx not in columns:
                columns.append(idx)
    return columns


def _find_score_columns(rows: list[list[str]], width: int) -> list[int]:
    columns: list[int] = []
    for idx in range(width):
        if is_ordinal_column(rows, idx):
            continue
        header_hit = any(
            any(term in normalize_cell(_cell(row, idx)) for term in SCORE_HEADER_TERMS)
            for row in rows[:4]
        )
        # 章节/页码/编号/数量等负向表头列, 数字是定位/计量值而非分值,
        # 禁止凭数值分布当分值列(如"技术规范书对应章节"的 3/6/7/8)。
        negative_header = any(
            is_non_score_header(_cell(row, idx)) for row in rows[:4]
        )
        numeric_hits = 0
        long_numeric_hits = 0
        rule_semantic_rows = 0
        for row in rows[1:]:
            if is_total_row(row):
                continue
            cell = _cell(row, idx)
            if parse_short_score(cell) is not None:
                numeric_hits += 1
                if has_scoring_rule_semantics(row_text(row)):
                    rule_semantic_rows += 1
            elif re.search(r"\d+(?:\.\d+)?\s*分", str(cell or "")):
                long_numeric_hits += 1
        if header_hit and numeric_hits >= 1:
            columns.append(idx)
        elif (
            not negative_header
            # 第三元: 无表头语义支撑的纯分布列, 至少 1 个分值行需含评分规则语义
            # ("得X分/扣X分/N分/酌情"), 否则视为编号/计量列。
            and rule_semantic_rows >= 1
            and numeric_hits >= 2
            and numeric_hits > long_numeric_hits
        ):
            columns.append(idx)
    return columns


def _count_structural_item_rows(
    rows: list[list[str]], score_columns: list[int]
) -> int:
    """统计"评分对象+分值+评分规则语义"三元同行共现的行数。

 职责: 提供独立于表头词表的评分项行级结构信号，覆盖混排表/非常规表头措辞;
 三元缺一不计(: 仅"对象+数字"两元会把章节索引/编号表误判为评分表)。
 不做: 不识别列语义、不解析分值归属（scoring_table_parser 职责）。
 """
    if not score_columns:
        return 0
    count = 0
    for row in rows:
        if is_total_row(row):
            continue
        hit_col = next(
            (idx for idx in score_columns if parse_short_score(_cell(row, idx)) is not None),
            None,
        )
        if hit_col is None:
            continue
        if not any(_has_chinese_short_text(_cell(row, idx)) for idx in range(hit_col)):
            continue
        if not has_scoring_rule_semantics(row_text(row)):
            continue
        count += 1
    return count


def _looks_like_weight_summary(rows: list[list[str]]) -> bool:
    table_text = "\n".join(row_text(row) for row in rows)
    if not ("加权得分" in table_text and any(k in table_text for k in ("报价", "价格", "综合", "技术"))):
        return False
    weight_rows = 0
    for row in rows:
        row_joined = row_text(row)
        if not any(k in row_joined for k in ("报价", "价格", "综合", "技术")):
            continue
        if any(parse_weight_value(cell) is not None for cell in row):
            weight_rows += 1
    return weight_rows >= 2


def _looks_like_assessment_form(table_text: str) -> bool:
    """售后/履约考核表负例: 不是投标评分表。"""
    return "考核项目" in table_text and (
        "扣分事由" in table_text or "考核人" in table_text
    )


def looks_like_operational_assessment(text: str) -> bool:
    """履约/运营考核表软标记签名。

 职责: 识别食堂/月度服务质量等非评标考核表，供确认页建议排除。
 不做: 不拒绝评分候选（硬拒绝仍走 _looks_like_assessment_form）。
 """
    blob = str(text or "")
    if not blob:
        return False
    if any(anchor in blob for anchor in _BID_EVAL_ANCHORS):
        return False
    head = blob[:500]
    if "考核表" in head:
        return True
    return "考核项目" in blob and any(
        term in blob for term in _OPERATIONAL_ASSESSMENT_CONTEXT
    )


def _cell(row: list[str], idx: int) -> str:
    return str(row[idx] or "").strip() if 0 <= idx < len(row) else ""


def _has_chinese_short_text(text: str) -> bool:
    compact = normalize_cell(text)
    return bool(re.search(r"[\u4e00-\u9fff]", compact)) and 1 <= len(compact) <= 80


__all__ = [
    "BID_EVAL_ANCHORS",
    "ScoringTableAnalysis",
    "analyze_scoring_table_rows",
    "looks_like_operational_assessment",
]

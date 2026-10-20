"""扁平顶层评分轴检测器。

职责: 从二维网格检测无层级的扁平评分轴，提取顶层评分项(标签+分值)；
      供 volume_label 回填阶段在无 group 列时建立轴坐标。
不做: 不访问数据库/LLM；不拆分父子项；不验证总分是否为100；
      不调用解析器/声明模块；不处理续表/层级表；不使用体量关键词白名单。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Protocol

from app.core.extractor.scoring.column_role_inference import longest_text_column
from app.core.extractor.scoring.score_column_semantics import (
    HEADER_SCORE_TERMS,
    has_scoring_rule_semantics,
    is_ordinal_column,
    is_scoring_header_row,
    is_structural_score_item_name,
    is_total_row,
    normalize_cell,
    parse_score_range_max,
    parse_short_score,
    parse_short_score_tolerant,
)

# 符合性表表头信号（一票拒绝）
_CONFORMITY_TERMS: tuple[str, ...] = (
    "符合性",
    "是否符合",
    "是否满足",
    "符合标准",
    "资格审查",
    "初步审查",
)

# 独立分值表头单元格词（精确匹配），用于 headerless 续表鉴别
_SCORE_HEADER_EXACT: frozenset[str] = frozenset(
    t for t in HEADER_SCORE_TERMS if len(t) <= 4
) | frozenset({"分值", "满分", "分数", "配分", "赋分", "标准分"})

# 最小严格正分父项数
_MIN_POSITIVE_ROWS = 2

# 标签单元格最大归一化字符数（超过视为规则正文）
_MAX_LABEL_LEN = 30

# 序号前缀正则：支持（一）/(1)/1./一、格式；\d+\.(?!\d) 阻止 1.0 被误剥
_ORDINAL_PREFIX_RE = re.compile(
    r"^(?:"
    r"[（(][一二三四五六七八九十百\d]+[）)]\s*"   # （一）/ (1) / （1）
    r"|[一二三四五六七八九十百]+[、\.．。,，\s]+"  # 一、/ 一.
    r"|\d+[、，。\s]+"                            # 1、/ 1,
    r"|\d+\.(?!\d)"                             # 1. 后紧接非数字（避免 1.0）
    r")"
)


def _cell(row: list[str], idx: int) -> str:
    """安全取单元格文本。"""
    return str(row[idx] or "").strip() if 0 <= idx < len(row) else ""


@dataclass(frozen=True)
class FlatScoringAxisRow:
    """扁平顶层评分轴的单行记录。

    职责: 承载扁平评分表中每个顶层评分项的行号、标签与分值，
          供 volume_label 回填阶段建立轴坐标映射。
    不做: 不携带子项信息；不标注体量归属；不存储规则文本。
    """

    row_index: int  # 原始网格行号（0-based，含表头行）
    label: str      # 评分项标签（原文单元格内容，非空）
    score: float    # 该项分值（严格正浮点数）


class _FlatAxisLabelItem(Protocol):
    """扁平轴标签回填所需的最小评分项协议。"""

    score_item_name: str
    score_weight: float
    volume_label: str | None


def detect_flat_scoring_axis(grid: list[list[str]]) -> list[FlatScoringAxisRow]:
    """从二维网格提取扁平顶层评分轴行；不满足条件返回空列表。

    正例结构（三种典型形态）：
    - 评审项目(中文序号列)|评分内容|评分标准|空列|分值  → holdout31 识别 3 父项
    - 评分大类|分值|评分细则                          → validation01 识别 5 项
    - 序号|评分因素|分值|评分标准|说明                → sample33 投影后识别 7 项

    拒绝条件（任一触发即返回 []）：
    - 无有效表头行（headerless 续表）
    - 数据区存在 ≥2 个全宽组标题行（仅一格有短业务标签，无分值）
    - 标签列左侧存在任意非序号含业务文本的独立列（层级表，含稀疏 group）
    - 少于 2 个严格正分父项
    - 标签列含重复值（同名不同行）
    - 总行数不足 3（header + 至少 2 data rows）
    - 符合性表（是否符合/资格审查等标头信号）
    """
    if len(grid) < 3:
        return []

    header = grid[0]
    data_rows = grid[1:]
    width = max((len(r) for r in grid), default=0)

    if width < 2:
        return []

    if _is_conformity_header(header):
        return []

    if not _has_valid_header(header):
        return []

    score_col = _find_score_column(grid, width)
    if score_col is None:
        return []

    label_col = _find_label_column(grid, width, score_col)
    if label_col is None:
        return []

    # 仅 label 左侧父级列中的全宽标题才表示分段层级；规则列续写不计。
    if _count_section_title_rows(data_rows, score_col, label_col) >= 2:
        return []

    if _has_group_column(grid, width, label_col, score_col):
        return []

    rows = _extract_axis_rows(data_rows, label_col, score_col)

    if len(rows) < _MIN_POSITIVE_ROWS:
        return []

    # 统一去序号前缀：产出标签为清洁标签，供下游直接比对无需再剥
    clean_rows: list[FlatScoringAxisRow] = []
    for r in rows:
        clean = normalize_cell(_strip_ordinal_prefix(normalize_cell(r.label)))
        if not _is_valid_label_cell(clean):
            return []  # 去前缀后标签失效（空/结构词/纯数字）→ 拒绝整个轴
        clean_rows.append(FlatScoringAxisRow(row_index=r.row_index, label=clean, score=r.score))

    if len({r.label for r in clean_rows}) < len(clean_rows):
        return []  # 去前缀后标签碰撞 → 拒绝整个轴

    return clean_rows


def resolve_axis_label(
    name: str,
    score: float,
    axis_rows: list[FlatScoringAxisRow],
) -> str | None:
    """把 (name, score) 二元组映射到扁平轴标签原文；歧义或未找到返回 None。

    匹配策略：
    1. 精确匹配：normalize(label) == normalize(name) 且分值相等
    2. 后缀匹配：去序号前缀后的 normalize(label) == normalize(name) 且分值相等

    唯一性约束：若同时存在多个候选行（同名同分），返回 None（禁止静默取首个）。
    同分不同名的行通过精确 name 匹配区分，不会产生歧义。
    """
    target = normalize_cell(name)
    candidates: list[FlatScoringAxisRow] = []
    for row in axis_rows:
        if abs(row.score - score) > 0.001:
            continue
        row_norm = normalize_cell(row.label)
        if row_norm == target or _strip_ordinal_prefix(row_norm) == target:
            candidates.append(row)
    if len(candidates) == 1:
        return candidates[0].label
    return None  # 未找到或歧义


def backfill_flat_axis_labels(
    items: Iterable[_FlatAxisLabelItem],
    grid: list[list[str]],
) -> int:
    """为**无卷标签**的评分项按唯一匹配填入轴标签，返回实际改写数量。

    职责: 仅在网格通过严格扁平轴检测后，为空标签项按名称和分值建立轴坐标。
    不做: 不推断分组、不覆盖任何已有标签、不修改未匹配或多项竞争同一轴标签的评分项。

    只填空不覆盖（U5 门禁重设，2026-08-03）: 已有标签必定来自更有上下文的来源
    （group 列 / 声明对 / 卷解析），层级也更粗；"每项自成一卷"是信息量最低的假设，
    覆盖即降级。实测 sample_corpus/12 原持有 `技术商务部分`(70)/`报价部分`(30)
    两组权威卷标签，覆盖后账本组行 extracted_sum 由 70/30 归零、缺口变 -70/-30。
    唯一性判定仍在全部 items 上进行，避免只看空标签子集而放行本应竞争的匹配。
    """
    item_list = list(items)
    axis_rows = detect_flat_scoring_axis(grid)
    if not axis_rows:
        return 0

    normalized_labels = [
        normalize_cell(item.volume_label or "")
        for item in item_list
    ]
    existing_label_set = set(normalized_labels)
    axis_label_set = {normalize_cell(row.label) for row in axis_rows}
    if (
        normalized_labels
        and all(normalized_labels)
        and len(existing_label_set) < len(item_list)
        and existing_label_set.issubset(axis_label_set)
    ):
        return 0

    matches_by_label: dict[str, list[_FlatAxisLabelItem]] = {}
    for item in item_list:
        label = resolve_axis_label(
            item.score_item_name,
            item.score_weight,
            axis_rows,
        )
        if label is not None:
            matches_by_label.setdefault(label, []).append(item)

    updated = 0
    for label, matches in matches_by_label.items():
        if len(matches) != 1:
            continue
        item = matches[0]
        if normalize_cell(item.volume_label or ""):
            continue  # 已有标签一律保留（见 docstring「只填空不覆盖」）
        item.volume_label = label
        updated += 1
    return updated


# ── 私有辅助函数 ────────────────────────────────────────────────────────────


def _is_conformity_header(header: list[str]) -> bool:
    """表头含符合性/资格审查信号 → 是符合性表（一票否决）。"""
    text = "".join(normalize_cell(c) for c in header)
    return any(term in text for term in _CONFORMITY_TERMS)


def _has_valid_header(header: list[str]) -> bool:
    """表头是否有效（拦截 headerless 续表）。

    有效条件（任一满足）：
    - is_scoring_header_row 认定为评分表头签名行
    - 表头中存在某个单元格归一化后与分值词精确匹配（分值/满分/分数等）
    """
    if is_scoring_header_row(header):
        return True
    for cell in header:
        if normalize_cell(cell) in _SCORE_HEADER_EXACT:
            return True
    return False


def _find_score_column(grid: list[list[str]], width: int) -> int | None:
    """找分值列：非序号列中严格正分值行数最多且 >= _MIN_POSITIVE_ROWS 的列。

    grid 须含表头行，以便 is_ordinal_column 通过表头词正确排除序号列。
    """
    data_rows = grid[1:]
    best_col: int | None = None
    best_count = 0

    for col in range(width):
        if is_ordinal_column(grid, col):
            continue
        count = sum(
            1
            for row in data_rows
            if not is_total_row(row) and _parse_positive_score(_cell(row, col)) is not None
        )
        if count > best_count:
            best_count = count
            best_col = col

    return best_col if best_count >= _MIN_POSITIVE_ROWS else None


def _parse_positive_score(text: str) -> float | None:
    """解析严格正分值（短分值 + 水印容忍 + 区间上限三路径）。"""
    v = parse_short_score(text)
    if v is not None and v > 0:
        return v
    v = parse_short_score_tolerant(text)
    if v is not None and v > 0:
        return v
    v = parse_score_range_max(text)
    return v if v is not None and v > 0 else None


def _find_rule_col(data_rows: list[list[str]], width: int, score_col: int) -> int | None:
    """找规则列：最长文本且至少一格含评分规则语义的列。

    语义校验防止把只含短评分项名（技术方案/商务实力）的列误当规则列，
    从而保证稀疏 group 表两列都被视为业务文本列（触发层级拦截）。
    """
    candidate = longest_text_column(data_rows, width, exclude={score_col, None})
    if candidate is None:
        return None
    if any(has_scoring_rule_semantics(_cell(row, candidate)) for row in data_rows):
        return candidate
    return None


def _find_label_column(
    grid: list[list[str]], width: int, score_col: int
) -> int | None:
    """找标签列：从右到左搜索，返回第一个与正分值共现的唯一短标签列。

    "与正分值共现"（scored_hits）= 同行 score_col 也有正分值；
    这样可排除 holdout31 sub-rule 列（供货方案仅 1 共现行，不足 2）。
    右到左搜索确保在 group+item 表中优先找到靠近分值列的 item 列，
    从而使 group 列暴露在标签列左侧被 _has_group_column 捕捉。
    """
    data_rows = grid[1:]
    rule_col = _find_rule_col(data_rows, width, score_col)

    for col in range(width - 1, -1, -1):
        if col == score_col or col == rule_col:
            continue
        if is_ordinal_column(grid, col):
            continue

        scored_hits = [
            _cell(row, col)
            for row in data_rows
            if not is_total_row(row)
            and _is_valid_label_cell(_cell(row, col))
            and _parse_positive_score(_cell(row, score_col)) is not None
        ]
        if len(scored_hits) < _MIN_POSITIVE_ROWS:
            continue

        normalized = [normalize_cell(c) for c in scored_hits]
        if len(set(normalized)) < len(normalized):
            continue  # 标签有重复 → 不是有效 label 列

        return col

    return None


def _is_valid_label_cell(text: str) -> bool:
    """单元格文本是否可作为有效评分项标签。"""
    compact = normalize_cell(text)
    if not compact:
        return False
    if len(compact) > _MAX_LABEL_LEN:
        return False
    if is_structural_score_item_name(compact):
        return False
    if compact.isdigit():
        return False
    return True


def _has_group_column(
    grid: list[list[str]], width: int, label_col: int, score_col: int
) -> bool:
    """标签列左侧是否存在独立分组列（层级表信号）。

    新判据（泛化，不依赖重复值检测）：
    标签列左侧只要存在任意非序号、含业务文本的独立列，即视为层级表。
    holdout31/sample33 左侧序号列通过 is_ordinal_column(grid) 豁免；
    validation01 label 在首列时左侧为空，自动通过。
    """
    data_rows = grid[1:]
    for col in range(label_col):
        if col == score_col:
            continue
        if is_ordinal_column(grid, col):  # 完整 grid 保留表头语义（I1）
            continue
        has_business = any(
            _is_valid_label_cell(_cell(row, col))
            for row in data_rows
            if not is_total_row(row)
        )
        if has_business:
            return True
    return False


def _is_section_title_row(row: list[str], score_col: int, label_col: int) -> bool:
    """判断是否为全宽组标题行（分段层级表的分组头）。

    条件（全部满足）：
    1. 整行仅一格非空
    2. 该格是有效短业务标签（≤30字符，非结构词）
    3. 该格位于标签列左侧的父级侧
    4. 该格不在分值列（无独立分值）
    5. 该格不含评分规则语义
    """
    non_empty = [(i, normalize_cell(c)) for i, c in enumerate(row) if normalize_cell(c)]
    if len(non_empty) != 1:
        return False
    idx, text = non_empty[0]
    if idx >= label_col or idx == score_col:
        return False
    if not _is_valid_label_cell(text):
        return False
    if has_scoring_rule_semantics(text):
        return False
    return True


def _count_section_title_rows(
    data_rows: list[list[str]],
    score_col: int,
    label_col: int,
) -> int:
    """统计数据区全宽组标题行数量（排除合计行）。"""
    return sum(
        1 for row in data_rows
        if not is_total_row(row)
        and _is_section_title_row(row, score_col, label_col)
    )


def _extract_axis_rows(
    data_rows: list[list[str]],
    label_col: int,
    score_col: int,
) -> list[FlatScoringAxisRow]:
    """从数据行中提取有效的扁平轴行（row_index 从 1 起，含表头偏移）。"""
    result: list[FlatScoringAxisRow] = []
    for i, row in enumerate(data_rows):
        if is_total_row(row):
            continue
        score = _parse_positive_score(_cell(row, score_col))
        if score is None:
            continue
        raw_label = _cell(row, label_col)
        if not raw_label or is_structural_score_item_name(raw_label):
            continue
        result.append(FlatScoringAxisRow(
            row_index=i + 1,  # +1 偏移表头行（grid[0]）
            label=raw_label,
            score=score,
        ))
    return result


def _strip_ordinal_prefix(label: str) -> str:
    """去序号前缀：支持（一）/(1)/一、/1.格式；严禁剥离 1.0/1.5 等业务数字。"""
    m = _ORDINAL_PREFIX_RE.match(label)
    return label[m.end():] if m else label


__all__ = [
    "FlatScoringAxisRow",
    "backfill_flat_axis_labels",
    "detect_flat_scoring_axis",
    "resolve_axis_label",
]

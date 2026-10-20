"""列角色分布推断共享模块。

职责: 从表格行的数据分布推断分值列/规则列/评分对象列/分组列, 供列统计解析与逻辑表
 装配器的"表头-行双向验证"共用同一判据; 并收口分组文本到卷别的单源映射。
不做: 不判定表头签名(score_column_semantics 职责)、不产评分项(解析器职责)、
 不调用 LLM/DB。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.extractor.scoring.paired_score_column import (
    find_paired_header_columns,
    validate_and_select_max,
)
from app.core.extractor.scoring.score_column_semantics import (
    has_scoring_rule_semantics,
    is_ordinal_column,
    is_total_row,
    normalize_cell,
    parse_short_score_tolerant,
    row_text,
)
from app.core.extractor.scoring.volume_labeling import service_volume_signal

_ATTRIBUTE_VALUE_TERMS = {"是", "否", "主观", "客观", "扣分"}
# 明细表规则列表头信号(共用): 泛化词, 仅在"未分配角色的列"中检索。
_DETAIL_RULE_HEADER_TERMS = ("内容", "标准", "规则", "说明")
# 对象名列的数据资格上限: 词面绑定的对象列若均长超此值, 实为规则正文列。
_NAME_MEAN_LEN_LIMIT = 40.0


@dataclass(frozen=True)
class ColumnRoles:
    """列角色(分布推断产物恒有分值列; 表头定位产物允许无分值列, 如嵌入式括号分值表)。"""

    score: int | None
    rule: int | None
    name: int | None
    group: int | None


@dataclass(frozen=True)
class RoleResolution:
    """header 宽度组角色决策结果（归档 /Task2 收口点）。

 供 assembler 无条件消费：resolved_roles 用于 parser 路径（seg.resolved_roles），
 canonical_roles（若非 None）覆盖 _group_roles[width]（canonical 渲染路径），
 repair_forced=True 时 assembler 应设 seg.repair_applied=True。
 """

    resolved_roles: ColumnRoles
    canonical_roles: ColumnRoles | None = None
    repair_forced: bool = False


def infer_column_roles(
    rows: list[list[str]], *, min_score_rows: int = 3
) -> ColumnRoles | None:
    """从行分布推断列角色; 分值列或对象列不可辨时返回 None。

 判据(继承):
 - 分值列 = 最右侧非序号、且 >=min_score_rows 行为独立短分值的列, 且该列短分值行
 中至少 1 行含评分规则语义(拦截编号/章节/计量列);
 - 规则列 = 文本总长度最大的列;
 - 评分对象列 = 分值/规则列以外、含最多短中文对象名的最右列;
 - 分组列 = 对象列左侧含短中文的列。
 """
    if not rows:
        return None
    width = max((len(r) for r in rows), default=0)
    if width < 2:
        return None
    score_col = find_numeric_score_column(rows, width, min_hits=min_score_rows)
    if score_col is None or not column_has_rule_semantics(rows, score_col):
        return None
    rule_col = longest_text_column(rows, width, exclude={score_col})
    name_col = rightmost_short_chinese_column(rows, width, exclude={score_col, rule_col})
    if name_col is None and rule_col is not None:
        name_col = rightmost_short_chinese_column(
            rows,
            width,
            exclude={score_col, rule_col},
            min_hits=1,
            before_idx=rule_col,
        )
    if name_col is None:
        return None
    group_col = group_column(rows, width, name_col, exclude={score_col, rule_col})
    return ColumnRoles(score=score_col, rule=rule_col, name=name_col, group=group_col)


def column_has_rule_semantics(rows: list[list[str]], score_col: int) -> bool:
    """分值列至少一行具备评分规则语义（第三元, 列级门槛）。"""
    return any(
        not is_total_row(row)
        and parse_short_score_tolerant(_cell(row, score_col)) is not None
        and has_scoring_rule_semantics(row_text(row))
        for row in rows
    )


def find_numeric_score_column(
    rows: list[list[str]], width: int, *, min_hits: int = 3
) -> int | None:
    """最右侧非序号、且 >=min_hits 行为独立短分值的列作为分值列。

 分值命中统计使用水印噪声容忍解析: 被水印字污染的分值行若不计入命中,
 污染行占多数的表会整列识别失败、全表漏提。

 极小接线(归档 /Task3): 当 rows[0] 为含明确成对最大/最小值表头时,
 委托 paired_score_column 选列; 无成对表头则回退旧逻辑。
 """
    if rows:
        paired = _try_paired_column(rows, width, min_hits)
        if paired is not None:
            return paired
    for idx in range(width - 1, -1, -1):
        if is_ordinal_column(rows, idx):
            continue
        hits = sum(
            1
            for row in rows[1:]
            if not is_total_row(row)
            and parse_short_score_tolerant(_cell(row, idx)) is not None
        )
        if hits >= min_hits:
            return idx
    return None


def _try_paired_column(
    rows: list[list[str]], width: int, min_hits: int
) -> int | None:
    """极小接线: rows[0] 有成对max/min表头时返回验证后的max列索引，否则None。"""
    pair = find_paired_header_columns(rows[0])
    if pair is None:
        return None
    max_idx, min_idx = pair
    if max_idx >= width or min_idx >= width:
        return None
    return validate_and_select_max(max_idx, min_idx, rows[1:], min_score_rows=min_hits)


def longest_text_column(
    rows: list[list[str]], width: int, *, exclude: set[int | None]
) -> int | None:
    best_idx: int | None = None
    best_len = -1
    for idx in range(width):
        if idx in exclude:
            continue
        total = sum(len(normalize_cell(_cell(row, idx))) for row in rows)
        if total > best_len:
            best_len = total
            best_idx = idx
    return best_idx


def rightmost_short_chinese_column(
    rows: list[list[str]],
    width: int,
    *,
    exclude: set[int | None],
    min_hits: int = 2,
    before_idx: int | None = None,
) -> int | None:
    """分值/规则列以外、含最多"短中文对象名"且不带规则语义的最右列。

    S2/S3: 名称元与规则元互斥；当常规 hits>=2 不可得且 score/rule 已锚定时，
    调用方可把 min_hits 降为 1 并限制在 rule 列左侧，接住合并单元格稀疏真名列。
    """
    best_idx: int | None = None
    for idx in range(width):
        if idx in exclude:
            continue
        if before_idx is not None and idx >= before_idx:
            continue
        hits = sum(
            1
            for row in rows[1:]
            if not is_total_row(row) and is_short_chinese_name(_cell(row, idx))
        )
        if hits >= min_hits and _is_valid_name_column(rows, idx):
            best_idx = idx  # 取最右满足者
    return best_idx


def _is_valid_name_column(rows: list[list[str]], idx: int) -> bool:
    """对象名列必须具备足够值多样性, 且不得以评分规则语义为主体。

 职责: 排除是/否/主观/客观等属性列和"最低分/不得分/须提供"
 等规则说明列（/M4 S2）。
 不做: 不降低命中门槛；低命中场景由 rightmost_short_chinese_column 的调用方显式控制。
 """
    values = [
        normalize_cell(_cell(row, idx))
        for row in rows[1:]
        if not is_total_row(row) and normalize_cell(_cell(row, idx))
    ]
    if not values:
        return False
    attr_hits = sum(1 for value in values if value in _ATTRIBUTE_VALUE_TERMS)
    if attr_hits >= max(2, (len(values) + 1) // 2):
        return False
    short_values = [value for value in values if is_short_chinese_name(value)]
    semantic_hits = sum(1 for value in short_values if has_scoring_rule_semantics(value))
    if short_values and semantic_hits >= (len(short_values) + 1) // 2:
        return False
    distinct = set(values)
    return len(distinct) >= 3 or (len(distinct) / max(len(values), 1)) >= 0.5


def group_column(
    rows: list[list[str]], width: int, name_col: int, *, exclude: set[int | None]
) -> int | None:
    """评分对象列左侧、含短中文的列作为分组(卷别)列。"""
    for idx in range(name_col - 1, -1, -1):
        if idx in exclude:
            continue
        # 归档 /Task2: 序号列（中文/阿拉伯序号）不得被选为分组列
        if is_ordinal_column(rows, idx):
            continue
        hits = sum(
            1
            for row in rows[1:]
            if not is_total_row(row) and is_short_chinese_name(_cell(row, idx))
        )
        if hits >= 1:
            return idx
    return None


def infer_detail_rule_column(
    header: list[str],
    rows: list[list[str]],
    *,
    score_col: int,
    exclude: set[int | None],
) -> int | None:
    """明细表规则列推断: 已分配角色之外定位评审规则文本列。

 职责: 表头含 内容/标准/规则/说明 的未分配列直接命中(先分值列右侧、再左侧就近,
 兼容"评分项目|评分内容|分值"与"评审项目|分值|评审内容"两种排列);
 无表头信号时按数据分布兜底——文本量最大的未分配列, 且至少2行具备
 评分规则语义(得X分/N分/酌情)才采纳, 防把属性列/证明材料列当规则列。
 不做: 不判定分值列/对象列(infer_column_roles 职责)、不解析评分项。
 """
    assigned = {idx for idx in exclude if idx is not None}
    width = max(max((len(r) for r in rows), default=0), len(header))
    right = [i for i in range(score_col + 1, width) if i not in assigned]
    left = [i for i in range(score_col - 1, -1, -1) if i not in assigned]
    for idx in (*right, *left):
        head = normalize_cell(header[idx]) if idx < len(header) else ""
        if head and any(term in head for term in _DETAIL_RULE_HEADER_TERMS):
            return idx
    fallback = longest_text_column(rows, width, exclude=set(assigned))
    if fallback is None:
        return None
    rule_hits = sum(
        1
        for row in rows
        if not is_total_row(row) and has_scoring_rule_semantics(_cell(row, fallback))
    )
    return fallback if rule_hits >= 2 else None


def is_short_chinese_name(text: str) -> bool:
    compact = normalize_cell(text)
    return bool(re.search(r"[\u4e00-\u9fff]", compact)) and 1 <= len(compact) <= 30


def column_mean_text_length(rows: list[list[str]], col: int) -> float:
    """列非空单元格的平均文本长(角色资格赛的数据证据)。"""
    lengths = [
        len(normalize_cell(_cell(row, col)))
        for row in rows
        if not is_total_row(row) and normalize_cell(_cell(row, col))
    ]
    if not lengths:
        return 0.0
    return sum(lengths) / len(lengths)


def column_nonempty_count(rows: list[list[str]], col: int) -> int:
    """列非空单元格数量(对象名列空置资格赛证据)。"""
    return sum(
        1
        for row in rows
        if not is_total_row(row) and normalize_cell(_cell(row, col))
    )


def validate_name_rule_orientation(
    rows: list[list[str]],
    *,
    name_col: int | None,
    rule_col: int | None,
) -> bool:
    """name/rule 角色的数据资格赛: 词面绑定必须经数据分布确认。

 职责: 否决"对象名列均长超限"或"对象名列比规则列更长"的反转绑定——项目1700
 表头词"评分内容"(实为规则列)被因素词表命中后, canonical 渲染把长规则
 写进评分因素列, 同组兄弟子项随之丢失；同时否决数据行全空的对象名列,
 迫使样本27这类跨页列错位回退分布推断。
 不做: 不重新分配角色(调用方决定回退分布推断或 fail-visible)。
 """
    if name_col is None:
        return True
    # name 列实为序号列时拒绝词面绑定（归档 /Task2: holdout31 项名取到 一/二/五）
    if is_ordinal_column(rows, name_col):
        return False
    data_rows = [row for row in rows if not is_total_row(row)]
    if len(data_rows) >= 3 and column_nonempty_count(data_rows, name_col) == 0:
        return False
    name_mean = column_mean_text_length(rows, name_col)
    if name_mean > _NAME_MEAN_LEN_LIMIT:
        return False
    if rule_col is not None:
        rule_mean = column_mean_text_length(rows, rule_col)
        if rule_mean > 0 and name_mean > rule_mean:
            return False
    return True


def resolve_header_roles_with_fallback(
    rows: list[list[str]],
    header_row: list[str],
    lexical_roles: ColumnRoles,
    score_supported: bool,
    orientation_ok: bool,
) -> RoleResolution:
    """header 宽度组双向验证 + 两条质量修正通路（归档 /Task2）。

 职责: orientation 失败时回退分布推断，序号 name 列位移时标记 repair_forced；
 orientation 通过但 rule 缺失时含 header 传推断，质量门禁后输出 canonical 路径。
 不做: 不判断 repair_applied 的其他触发条件（多宽度/gap/forward-fill/声明合成等）。
 """
    roles = lexical_roles
    repair_forced = False
    canonical_roles: ColumnRoles | None = None

    if not score_supported or not orientation_ok:
        # 归档 /Task3: 明确成对最大/最小值表头时含 header_row 传推断，
        # 使 find_numeric_score_column 能识别成对列并选最大值列。
        # 其他表保持原行为（仅传 data_rows），防宽稀疏表 rule 列错选。
        rows_for_infer = (
            [header_row, *rows]
            if find_paired_header_columns(header_row) is not None
            else rows
        )
        inferred = infer_column_roles(rows_for_infer, min_score_rows=2)
        if inferred is not None:
            if not orientation_ok and inferred.name != roles.name:
                # name 列位移（序号列被拒后推断出不同列）→ 强制 canonical 渲染
                repair_forced = True
            roles = inferred
        # inferred is None 且 orientation_ok=False: fail-visible，
        # 调用方负责记录警告并保留词面（roles 未改变）

    # rule_col 词面缺失通路：只补"分值轴已受支持、方向资格赛通过"的表。
    # score 资格赛失败已由上方全角色回退处理，不再用不同 rows 输入重复推断。
    if score_supported and orientation_ok and roles.rule is None and roles.name is not None:
        rule_fill = infer_column_roles([header_row, *rows], min_score_rows=2)
        if rule_fill is not None and rule_fill.rule is not None:
            lex_fill = column_nonempty_count(rows, roles.name)
            inf_fill = column_nonempty_count(rows, rule_fill.name)
            # 质量门禁: inf_fill < lex_fill 时推断填充更少，不替换
            # （宽稀疏表词面 name 列填充高于推断，不覆盖；
            #  validation02 词面 name 几乎为空，推断更优，合法替换）
            if inf_fill >= max(1, lex_fill):
                canonical_roles = rule_fill

    return RoleResolution(
        resolved_roles=roles,
        canonical_roles=canonical_roles,
        repair_forced=repair_forced,
    )


def volume_from_group_text(text: str, volume_default: str) -> str:
    """分组/因素文本 → 卷别（单源映射, 消除多处漂移）。: 补 service/qualification 卷与"评审价/基准价"价格信号; 技术先于服务判定,
 保证"技术服务部分"归 technical 不被泛化的服务信号错抢。
 """
    t = (text or "").replace(" ", "")
    if any(k in t for k in ("报价", "价格", "评标价", "评审价", "基准价")):
        return "price"
    if "商务" in t:
        return "commercial"
    if "技术" in t:
        return "technical"
    if service_volume_signal(t):
        return "service"
    if any(k in t for k in ("资信", "信用", "信誉")):
        return "qualification"
    return volume_default


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


__all__ = [
    "ColumnRoles",
    "RoleResolution",
    "column_has_rule_semantics",
    "column_nonempty_count",
    "find_numeric_score_column",
    "group_column",
    "infer_column_roles",
    "infer_detail_rule_column",
    "is_short_chinese_name",
    "longest_text_column",
    "resolve_header_roles_with_fallback",
    "rightmost_short_chinese_column",
    "validate_name_rule_orientation",
    "volume_from_group_text",
]

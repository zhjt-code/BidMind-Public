"""表头列角色的词面定位与绑定决策（归档 /Task2 移植配套抽取）。

职责: 从表头行词面定位各列角色，并对词面结论执行双向验证、委托
 column_role_inference 完成回退与补全，按决策形态输出可定位的诊断日志。
不做: 不做角色推断本身（column_role_inference 职责）、不改段状态、不解析表格。

为什么独立成模块:
 scoring_table_assembler.py 已达复杂度护栏 Hard 阈值（800 行），
 按护栏「新增能力必须新建子模块」，本次移植的决策与日志逻辑不得堆入 assembler。
 词面定位（roles_from_header 及其词表）与本模块职责同源，一并迁入。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.core.extractor.scoring.column_role_inference import (
    ColumnRoles,
    RoleResolution,
    resolve_header_roles_with_fallback,
    validate_name_rule_orientation,
)
from app.core.extractor.scoring.score_column_semantics import (
    HEADER_SCORE_TERMS,
    normalize_cell,
)

__all__ = [
    "HeaderBindingContext",
    "bind_header_width_roles",
    "find_header_col",
    "roles_from_header",
]

# 表头列角色词(与 is_scoring_header_row 同一词面, 用于表头内定位列位置)
# 细分项列是子项对象名的最强信号(1504 父级"评分因素"列左置), 单独优先扫描。
_FACTOR_TERMS_PRIMARY = ("细分项", "计分因素")
_FACTOR_TERMS_STRONG = (
    "评分项目",
    "评分项",
    "评分对象",
    "评审项目",
    "评分因素",
    "评审因素",
    "评议因素",
    "二级评审子类",
    "评议内容",
)
_FACTOR_TERMS_GENERIC = ("项目", "内容", "评审内容", "评分内容")
_RULE_TERMS = (
    "评分标准",
    "评审标准",
    "评议标准",
    "评定标准",
    "规则",
    "计分标准",
    "评定内容及标准",
    "主要内容",
)
_GROUP_TERMS = ("评审内容", "评审大类", "评议大类")
_WEIGHT_TERMS = ("权重", "占比")
_CLAUSE_TERMS = ("条款",)
_HEADER_CELL_MAX_LEN = 10


def roles_from_header(header: list[str]) -> tuple[ColumnRoles, int | None, int | None]:
    """从表头行定位列角色; 返回(角色, 权重列, 条款列)。"""
    factor = find_header_col(header, _FACTOR_TERMS_PRIMARY)
    if factor is None:
        factor = find_header_col(header, _FACTOR_TERMS_STRONG)
    if factor is None:
        factor = find_header_col(header, _FACTOR_TERMS_GENERIC, exact=True)
    rule = find_header_col(header, _RULE_TERMS)
    weight = find_header_col(header, _WEIGHT_TERMS)
    clause = find_header_col(header, _CLAUSE_TERMS)
    score = None
    for idx, cell in enumerate(header):
        compact = normalize_cell(cell)
        if not compact or len(compact) > _HEADER_CELL_MAX_LEN or idx == weight:
            continue
        if any(t in compact for t in _WEIGHT_TERMS):
            continue
        if any(t in compact for t in HEADER_SCORE_TERMS):
            score = idx
            break
    group = None
    for idx, cell in enumerate(header):
        compact = normalize_cell(cell)
        if idx == factor or not compact or len(compact) > _HEADER_CELL_MAX_LEN:
            continue
        if any(t in compact for t in _GROUP_TERMS) or (
            factor is not None and idx < factor and any(t in compact for t in _FACTOR_TERMS_STRONG)
        ):
            group = idx
            break
    return ColumnRoles(score=score, rule=rule, name=factor, group=group), weight, clause


def find_header_col(
    header: list[str], terms: tuple[str, ...], *, exact: bool = False
) -> int | None:
    for idx, cell in enumerate(header):
        compact = normalize_cell(cell)
        if not compact or len(compact) > _HEADER_CELL_MAX_LEN:
            continue
        if exact:
            if compact in terms:
                return idx
        elif any(t in compact for t in terms):
            return idx
    return None


@dataclass(frozen=True)
class HeaderBindingContext:
    """日志定位上下文（收敛入参个数，满足复杂度护栏参数上限）。"""

    page_start: int
    page_end: int
    header_structure: Any | None


def bind_header_width_roles(
    rows: list[list[str]],
    header_row: list[str],
    lexical_roles: ColumnRoles,
    score_supported: bool,
    context: HeaderBindingContext,
) -> RoleResolution:
    """双向验证表头词面角色并返回最终决策（含诊断日志）。

 双向验证: 分值列须有行分布支持（由调用方以 score_supported 传入）+
 name/rule 词面须过数据资格赛（: "评分内容"词面实为规则列时，
 词面绑定会把长规则写进评分因素列，canonical 渲染随之反转）。
 任一不过 → 回退分布推断；分布也不可辨 → fail-visible 保留词面并告警。
 """
    orientation_ok = validate_name_rule_orientation(
        rows, name_col=lexical_roles.name, rule_col=lexical_roles.rule
    )
    resolution = resolve_header_roles_with_fallback(
        rows, header_row, lexical_roles, score_supported, orientation_ok
    )

    if not orientation_ok:
        if resolution.resolved_roles != lexical_roles:
            logger.info(
                "[文档处理] 表头词面列角色未过数据资格赛, 改用分布推断"
                ": 词面(name={},rule={}) → 分布(name={},rule={}) 结构证据: {}",
                lexical_roles.name, lexical_roles.rule,
                resolution.resolved_roles.name, resolution.resolved_roles.rule,
                context.header_structure,
            )
        else:
            logger.warning(
                "[文档处理] 列角色词面与分布推断均不可信, 保留词面绑定"
                "(疑似列角色错位, 请人工核对): pages={}-{} 结构证据: {}",
                context.page_start, context.page_end, context.header_structure,
            )

    if resolution.canonical_roles is not None:
        logger.info(
            "[文档处理] rule_col 词面缺失分布补全 rule={} pages={}-{}",
            resolution.canonical_roles.rule, context.page_start, context.page_end,
        )

    return resolution

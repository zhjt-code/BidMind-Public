"""评分网格叶子清单（叶子粒度门禁的确定性基准）。

职责: 从 scoring chunk 网格确定性构建"带分值叶子清单"——结构性续行缝合(跨页
 折行)、三形态分值信号(独立分值格/内嵌括注/规则尾区间)、fail-closed 叶子
 名判定——并提供覆盖差集与组头形态判定(组头行不得作为叶子)。
不做: 不修改 parser/网格落库、不裁决 verdict/needs_review、不调用 LLM/DB、
 不做标签归一之外的语义推断(歧义行一律跳过, 宁漏不误报)。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.core.extractor.scoring.score_column_semantics import (
    find_embedded_scores,
    normalize_cell,
    parse_score_range_max,
    parse_short_score_tolerant,
)

# 组语义标准形态: 以这些词收尾的"项名"是表节头(组), 按构造不是叶子。
_STANDARD_GROUP_SUFFIXES = ("评分标准", "评审标准", "评标标准")
# 声明同键回声的组尾缀(从严: 不含"评审/项/书", 防误拦"价格评审"类真叶子)。
_ECHO_GROUP_SUFFIXES = ("标", "部分", "卷")
_ANNOTATION_PREFIXES = ("注：", "注:", "备注")
# 结构词: 表头/声明行特征, 此类行禁止被缝合进叶子, 也不作叶子名。
_STRUCTURAL_TERMS = ("分值构成", "评分因素", "评审因素", "总分", "合计")
_HEADER_EXACT_WORDS = ("评分标准", "评审标准", "评分内容", "评审内容", "序号", "条款号", "分值", "评分项")
# 条款号/序号形态: 纯数字、点号、括号、中文数字的组合(如 "2.2.4 （1）")。
_CLAUSE_LIKE_RE = re.compile(r"^[0-9一二三四五六七八九十.．、()\s\-]+$")
# 规则尾区间信号: 规则长文本以 "0-5分" 类区间收尾/内嵌(取末个区间上限)。
_TRAILING_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~～—–至]\s*(\d+(?:\.\d+)?)\s*分")
# 规则语义提示词: 含这些词且较长的格是规则文本, 不作叶子名。
_RULE_HINT_TERMS = ("得", "扣", "评委", "酌情", "满足", "要求", "评审时")
_RULE_HINT_MIN_LEN = 10
_TRAILING_PAREN_SCORE_RE = re.compile(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_MAX_RANGE_SCORE = 200.0
_MIN_COVERAGE_NAME_LEN = 4


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).lower()


def _strip_trailing_paren_score(text: str) -> str:
    return _TRAILING_PAREN_SCORE_RE.sub("", str(text or "")).strip()


def is_group_header_name(name: str) -> bool:
    """项名是否为组语义标准形态(X评分标准/评审标准/评标标准, 允许尾随括注分值)。"""
    stripped = normalize_cell(_strip_trailing_paren_score(name))
    return bool(stripped) and stripped.endswith(_STANDARD_GROUP_SUFFIXES)


def _is_group_label_cell(cell: str) -> bool:
    """格文本是否为组标签形态(标准形态, 或短组名尾缀如 商务标/技术部分/资信卷)。"""
    stripped = normalize_cell(_strip_trailing_paren_score(cell))
    if not stripped:
        return False
    if stripped.endswith(_STANDARD_GROUP_SUFFIXES):
        return True
    return len(stripped) <= 8 and stripped.endswith(_ECHO_GROUP_SUFFIXES)


def group_header_echo_names(
    name_scores: list[tuple[str, float | None]],
    declared_norm_keys: set[str],
    exempt_norm_keys: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    """检出"组头行自证"项名: 计分中的项名是组语义形态, 或与声明组同键的组名。

 根因: 这类行既是提取项又是声明组本身, 组轴 gap 恒 0 循环自证;
 它们按构造不是叶子, 计入通过依据即假绿。
 豁免: exempt_norm_keys 为网格中提不出叶子的组(unresolved_group_
 rule_rows, 分值藏在规则长句)——组行即该组唯一真实计分行, 不判自证;
 有清单叶子的组(父组冒充形态)不在豁免集, 仍必拦。
 """
    from app.core.extractor.scoring.ledger.label_normalization import (
        normalize_group_label,
    )

    echo: list[str] = []
    for name, score in name_scores:
        try:
            if score is None or float(score) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        clean = str(name or "").strip()
        if not clean:
            continue
        if exempt_norm_keys and normalize_group_label(clean) in exempt_norm_keys:
            continue
        if is_group_header_name(clean):
            echo.append(clean)
            continue
        stripped = normalize_cell(_strip_trailing_paren_score(clean))
        if (
            declared_norm_keys
            and normalize_group_label(clean) in declared_norm_keys
            and stripped.endswith(_ECHO_GROUP_SUFFIXES)
        ):
            echo.append(clean)
    return list(dict.fromkeys(echo))


def _cell_score_signal(cell: str) -> float | None:
    """格内分值信号: 短分值 / 整格区间 / 规则尾区间(取末个区间上限)。"""
    value = parse_short_score_tolerant(cell)
    if value is not None:
        return value
    value = parse_score_range_max(cell)
    if value is not None:
        return value
    last = None
    for match in _TRAILING_RANGE_RE.finditer(normalize_cell(cell)):
        last = match
    if last is None:
        return None
    try:
        low, high = float(last.group(1)), float(last.group(2))
    except (TypeError, ValueError):
        return None
    if low < 0 or high <= low or high > _MAX_RANGE_SCORE:
        return None
    return high


def _row_has_score_signal(cells: list[str]) -> bool:
    for cell in cells:
        if _cell_score_signal(cell) is not None:
            return True
        # 组头括注(35分)也算信号: 该行是新逻辑行, 不是上一行的续行
        if find_embedded_scores(cell) or _TRAILING_PAREN_SCORE_RE.search(str(cell or "")):
            return True
    return False


def _row_is_annotation(cells: list[str]) -> bool:
    return any(str(c or "").strip().startswith(_ANNOTATION_PREFIXES) for c in cells)


def _row_is_structural(cells: list[str]) -> bool:
    for cell in cells:
        compact = normalize_cell(cell)
        if not compact:
            continue
        if compact in _HEADER_EXACT_WORDS:
            return True
        if any(term in compact for term in _STRUCTURAL_TERMS):
            return True
    return False


def _is_continuation_row(cells: list[str]) -> bool:
    """结构判据续行: 首格(条款号位)空 + 全行无分值信号 + 非注释/结构行。"""
    if not any(str(c or "").strip() for c in cells):
        return False
    if str(cells[0] or "").strip():
        return False
    if _row_is_annotation(cells) or _row_is_structural(cells):
        return False
    return not _row_has_score_signal(cells)


def stitch_grid_continuation_rows(grid: list[list[Any]]) -> list[list[str]]:
    """把跨页/拆行断开的续行按同列拼回上一行(可迭代, 只在内存内, 不改落库)。"""
    stitched: list[list[str]] = []
    for raw in grid or []:
        cells = [str(c or "") for c in (raw if isinstance(raw, list) else [])]
        if stitched and _is_continuation_row(cells):
            prev = stitched[-1]
            while len(prev) < len(cells):
                prev.append("")
            for idx, cell in enumerate(cells):
                fragment = cell.strip()
                if not fragment:
                    continue
                prev[idx] = (prev[idx].rstrip() + fragment) if prev[idx].strip() else fragment
            continue
        stitched.append(cells)
    return stitched


def _is_rule_like(compact: str) -> bool:
    return len(compact) >= _RULE_HINT_MIN_LEN and any(
        term in compact for term in _RULE_HINT_TERMS
    )


def _leaf_from_row(cells: list[str]) -> tuple[str, float] | None:
    """单行叶子判定(fail-closed): 名与分值信号唯一可定位才入册, 歧义即跳过。"""
    # 形态1: 内嵌括注 "对象名（N分）"——名与分值同格, 无歧义
    embedded: list[tuple[str, float]] = []
    for cell in cells:
        for name, score in find_embedded_scores(cell):
            if not is_group_header_name(name) and not _is_group_label_cell(name):
                embedded.append((name, score))
    if len(embedded) == 1:
        return embedded[0]
    if len(embedded) > 1:
        return None
    # 形态2/3: 独立分值格或规则尾区间——分值格须唯一(首格条款号位除外)
    score_cells = [
        (idx, signal)
        for idx, cell in enumerate(cells)
        if not (idx == 0 and _CLAUSE_LIKE_RE.match(normalize_cell(cell) or "-"))
        and not _is_group_label_cell(cell)
        and (signal := _cell_score_signal(cell)) is not None
    ]
    if len(score_cells) != 1:
        return None
    score_idx, score = score_cells[0]
    candidates: list[str] = []
    for idx, cell in enumerate(cells):
        if idx == score_idx:
            continue
        compact = normalize_cell(cell)
        if not compact or len(_CJK_RE.findall(compact)) < 2:
            continue
        if _CLAUSE_LIKE_RE.match(compact):
            continue
        if compact.startswith(_ANNOTATION_PREFIXES) or compact in _HEADER_EXACT_WORDS:
            continue
        if any(term in compact for term in _STRUCTURAL_TERMS):
            continue
        if _is_group_label_cell(cell) or is_group_header_name(cell):
            continue
        if _is_rule_like(compact):
            continue
        candidates.append(compact)
    if len(candidates) != 1:
        return None
    name = candidates[0]
    if not (2 <= len(name) <= 40):
        return None
    return (name, float(score))


def leaf_inventory_from_grid(grid: list[list[Any]]) -> tuple[tuple[str, float], ...]:
    """网格 -> 带分值叶子清单 [(名, 分值)]; 注释/表头/歧义行按构造不入册。"""
    leaves: list[tuple[str, float]] = []
    for cells in stitch_grid_continuation_rows(grid or []):
        if _row_is_annotation(cells):
            continue
        leaf = _leaf_from_row(cells)
        if leaf is not None:
            leaves.append(leaf)
    return tuple(leaves)


def leaf_inventory_from_chunks(chunks: list[Any]) -> tuple[tuple[str, float], ...]:
    """从 scoring chunk 的 payload.grid 汇集叶子清单; 无载荷网格则为空(不断言)。"""
    from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk

    leaves: list[tuple[str, float]] = []
    for chunk in chunks or []:
        payload = payload_from_chunk(chunk)
        grid = (payload or {}).get("grid")
        if grid:
            leaves.extend(leaf_inventory_from_grid(grid))
    return tuple(leaves)


def unresolved_group_rule_rows(grid: list[list[Any]]) -> tuple[tuple[str, str], ...]:
    """提不出叶子的组行 -> (组格原文, 伴随规则原文) 对(反馈增强用)。

 形态如「商务标评分标准（50分）‖ 投标总报价与评标基准价相比…」——分值藏在
 长规则句里, 清单 fail-closed 提不出叶子名, 缺项点名帮不到该组; 把原文行
 交给重询反馈, 逼模型按原文输出该组计分行。
 """
    hints: list[tuple[str, str]] = []
    for cells in stitch_grid_continuation_rows(grid or []):
        if _row_is_annotation(cells) or _leaf_from_row(cells) is not None:
            continue
        group_cell = next(
            (str(c or "").strip() for c in cells if _is_group_label_cell(c)), ""
        )
        if not group_cell:
            continue
        rule_cell = next(
            (
                str(c or "").strip()
                for c in cells
                if str(c or "").strip() != group_cell
                and len(_CJK_RE.findall(normalize_cell(c))) >= 10
            ),
            "",
        )
        if rule_cell:
            hints.append((group_cell, rule_cell))
    return tuple(hints)


def _covered(compact_name: str, extracted_compacts: list[str]) -> bool:
    return any(
        compact_name in extracted or extracted in compact_name
        for extracted in extracted_compacts
        if extracted
    )


def missing_leaf_names(
    inventory: tuple[tuple[str, float], ...] | list[tuple[str, float]],
    extracted_names: list[str],
) -> list[str]:
    """清单相对提取项名的缺失差集(compact 双向包含, 短名不断言, 保序去重)。"""
    extracted_compacts = [_compact(name) for name in extracted_names or []]
    missing: list[str] = []
    seen: set[str] = set()
    for name, _score in inventory or []:
        compact = _compact(name)
        if len(compact) < _MIN_COVERAGE_NAME_LEN or compact in seen:
            continue
        seen.add(compact)
        if not _covered(compact, extracted_compacts):
            missing.append(name)
    return missing


def covered_leaf_count(
    inventory_names: list[str] | tuple[str, ...],
    extracted_names: list[str],
) -> int:
    """清单名被提取项覆盖的数量(C 轴采纳决胜用, 口径与 missing 对偶)。"""
    extracted_compacts = [_compact(name) for name in extracted_names or []]
    count = 0
    seen: set[str] = set()
    for name in inventory_names or []:
        compact = _compact(name)
        if len(compact) < _MIN_COVERAGE_NAME_LEN or compact in seen:
            continue
        seen.add(compact)
        if _covered(compact, extracted_compacts):
            count += 1
    return count


__all__ = [
    "covered_leaf_count",
    "group_header_echo_names",
    "is_group_header_name",
    "leaf_inventory_from_chunks",
    "leaf_inventory_from_grid",
    "missing_leaf_names",
    "stitch_grid_continuation_rows",
    "unresolved_group_rule_rows",
]

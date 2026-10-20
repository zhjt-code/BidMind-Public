# -*- coding: utf-8 -*-
"""表作用域组标签解析。

职责: 以"合计行切分 + 作用域起始页表前标题行 + 作用域分值闭合"三重结构证据,
 在装配后的最终网格上解析每个物理表作用域的组标签与声明小计, 产出/应用
 scope_rows 载荷; 另提供 section_path 表级标题标签的收紧提取。
不做: 不产评分项、不改网格行、不判枚举卷、不做词面卷名联想(标题文本按结构
 位置取用, 采纳与否由分值闭合/无数字门禁决定)、不调用 LLM/DB。

为何需要(根因): 项目2479 的组身份只写在正文标题("5.2.1 商务评审"), 表格本身
无分组列; 两张同构表被装配进同一 chunk 后, 唯一的组边界证据是表尾"合计 100"
行与各自起始页的标题行。项目2480 的组身份在表标题("附表3 商务评审标准表"),
已有词面探测把它放进了 section_path, 但从未落到评分项标签上。
判据 fail-closed: 作用域分值不闭合、标题行找不到或不合格、分值列缺失, 一律
不产出该作用域(维持现状, 宁可无标签不可错标)。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.extractor.scoring.ledger.label_normalization import is_total_like_label
from app.core.extractor.scoring.score_column_semantics import (
    is_total_row,
    parse_adjustment_score,
    parse_score_range_max,
    parse_short_score_tolerant,
)

_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
_CLAUSE_PREFIX_RE = re.compile(r"^[0-9]+(?:[.．][0-9]+)*\s*")
_PAREN_GROUP_RE = re.compile(r"[（(][^（()）]*[)）]")
_SCORE_MENTION_RE = re.compile(r"\d+(?:\.\d+)?\s*分")
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_TITLE_LEN = 24
_CLOSURE_TOLERANCE = 0.01
_SECTION_PATH_DELIMITER = " · "
# I3: 表格结构说明句特征词——末段含这些词说明是"如下表所示/标准如下/规定如下"
# 之类的正文引导描述, 不是章节标题; 命中时拒绝末段, 回落首段(-I3)。
# 只收录结构性固定短语, 不扩成开放业务词表。
_TABLE_DESC_TERMS: frozenset[str] = frozenset([
    "如下表", "见下表", "评审因素及标准", "评分因素及标准", "表所示",
    "标准如下", "规定如下",
])


def attach_scope_rows_to_payload(
    chunk: Any,
    pages_by_num: dict[int, Any],
    *,
    scope_page_texts: dict[int, str] | None = None,
) -> None:
    """在装配层为 scoring chunk 载荷补写 scope_rows(结构+闭合证据齐备才写)。

    载荷缺网格/页映射/分值列角色时静默跳过——scope_rows 是增量证据, 缺失即
    回退既有标签路径, 不影响 chunk 主体。
    """
    from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk

    payload = payload_from_chunk(chunk)
    if not isinstance(payload, dict):
        return
    grid = payload.get("grid")
    row_pages = payload.get("row_pages")
    score_col = (payload.get("column_roles") or {}).get("score")
    if not grid or not row_pages or not isinstance(score_col, int):
        return
    # 生产页级降噪会把独立标题行与首个评分行合并；表格网格应继续使用
    # 降噪后的装配结果，但标题定位必须显式消费降噪前快照。
    page_texts = scope_page_texts or {
        num: str(getattr(page, "text", "") or "") for num, page in pages_by_num.items()
    }
    scopes = resolve_table_scopes(
        [[str(c or "") for c in row] for row in grid],
        list(row_pages),
        page_texts,
        score_col=score_col,
    )
    if scopes:
        payload["scope_rows"] = scopes


def resolve_table_scopes(
    grid: list[list[str]],
    row_pages: list[int | None],
    page_texts: dict[int, str],
    *,
    score_col: int,
) -> list[dict]:
    """合计行切分作用域并逐个验证(闭合+标题), 返回 scope_rows 字典列表。

    返回元素: {"start": 网格行起, "end": 网格行止(含), "label": 标题标签,
    "declared": 合计声明值}。任一验证不过的作用域不产出(其余作用域不受牵连)。
    """
    if len(row_pages) != len(grid):
        return []
    total_rows = [
        idx for idx, row in enumerate(grid)
        if is_total_row(row) and _total_row_value(row, score_col) is not None
    ]
    if not total_rows:
        return []
    header_compact = _compact("|".join(grid[0])).replace("|", "")

    scopes: list[dict] = []
    prev_end = -1
    for total_idx in total_rows:
        start = prev_end + 1
        declared = _total_row_value(grid[total_idx], score_col)
        member_rows = [
            i for i in range(start, total_idx)
            if not is_total_row(grid[i])
        ]
        summed = round(
            sum(parse_short_score_tolerant(_cell(grid[i], score_col)) or 0.0 for i in member_rows),
            4,
        )
        start_page = next(
            (row_pages[i] for i in member_rows if isinstance(row_pages[i], int)), None
        )
        prev_total_page = row_pages[prev_end] if prev_end >= 0 else None
        prev_declared = (
            _total_row_value(grid[prev_end], score_col) if prev_end >= 0 else None
        )
        prev_end = total_idx
        # I1: 无数据行的空壳作用域直接跳过, 不产出任何 scope。
        if not member_rows:
            continue
        if declared is None:
            continue
        # declared<=0(惩罚/调整表)仅作结构边界, 不用正分sum与负总做闭合比较;
        # declared>0 的普通作用域仍严格闭合(fail-closed 不变)。
        if declared > 0 and abs(summed - declared) > _CLOSURE_TOLERANCE:
            continue  # 正分作用域不闭合 → 拒绝
        if start_page is None:
            continue
        label = _scope_title_from_page(
            page_texts.get(start_page, ""),
            header_compact,
            after_total_value=(
                prev_declared if prev_total_page == start_page else None
            ),
        )
        if not label:
            continue
        scopes.append(
            {"start": start, "end": total_idx, "label": label, "declared": declared}
        )
    return scopes


def scope_labels_for_data_rows(
    scope_rows: Any, data_offset: int, data_len: int
) -> dict[int, str]:
    """scope_rows(网格行序) -> 数据行序标签映射; 形状非法返回空映射。"""
    labels: dict[int, str] = {}
    if not isinstance(scope_rows, list):
        return labels
    for scope in scope_rows:
        if not isinstance(scope, dict):
            continue
        label = str(scope.get("label") or "").strip()
        try:
            start = int(scope.get("start"))
            end = int(scope.get("end"))
        except (TypeError, ValueError):
            continue
        if not label:
            continue
        for grid_idx in range(start, end + 1):
            data_idx = grid_idx - data_offset
            if 0 <= data_idx < data_len:
                labels[data_idx] = label
    return labels


def section_title_scope_label(section_path: str) -> str | None:
    """从 section_path 的表级标题后缀提取整表兜底标签(收紧口径)。

 只认 `_join_section_path` 约定的 " · " 后缀(词面标题探测产物); 剥括注与
 条款号后**必须不含任何分值/数字**——"详细评审(商务) 满分:20.00分"这类带
 数值的标题形态与卷定义声明同源, 直接作标签会抢占卷定义回填(语义),
 一律拒绝。
 """
    raw = str(section_path or "")
    if _SECTION_PATH_DELIMITER not in raw:
        return None
    tail = raw.rsplit(_SECTION_PATH_DELIMITER, 1)[1]
    cleaned = _PAREN_GROUP_RE.sub("", _compact(tail))
    cleaned = _CLAUSE_PREFIX_RE.sub("", cleaned).strip("：:；;、，,。")
    if not (2 <= len(cleaned) <= _MAX_TITLE_LEN):
        return None
    if re.search(r"\d", cleaned) or is_total_like_label(cleaned):
        return None
    if len(_CHINESE_RE.findall(cleaned)) < 2:
        return None
    return cleaned


def apply_section_title_scope_label(
    items: list[Any], section_path: str, *, has_volume_defs: bool
) -> None:
    """整表标题兜底: 仅当表内**零标签、零卷定义**时把标题标签盖到全部评分项。

    存在任一已有标签(更细粒度的结构/词面证据)或卷定义(声明侧稍后回填标签)时
    一律不介入——标题是最弱的组证据, 只填真空, 不与任何既有来源竞争。
    """
    if not items or has_volume_defs:
        return
    if any(getattr(item, "volume_label", None) for item in items):
        return
    label = section_title_scope_label(section_path)
    if not label:
        return
    for item in items:
        item.volume_label = label


def _total_row_value(row: list[str], score_col: int) -> float | None:
    """合计行声明值: 优先分值列, 否则要求全行唯一数值格(歧义即放弃)。

 识别顺序:
 1. parse_short_score_tolerant → 正分值(已有路径)
 2. parse_adjustment_score → 负/零分值(复用单源)
 3. parse_score_range_max → 纯正区间上限(如 0～30)
 4. _parse_negative_range_lower → 含负下界区间(如 -10～2), 取负下界
 5. 全行唯一值 fallback(以上函数之一)
 """
    cell = _cell(row, score_col)
    direct = parse_short_score_tolerant(cell)
    if direct is not None:
        return direct
    adj = parse_adjustment_score(cell)
    if adj is not None:
        return adj
    range_max = parse_score_range_max(cell)
    if range_max is not None:
        return range_max
    neg_lower = _parse_negative_range_lower(cell)
    if neg_lower is not None:
        return neg_lower
    # 全行唯一值 fallback: 取正/负/区间(含负区间)均纳入候选(Minor)
    values: set[float] = set()
    for c in row:
        s = str(c or "")
        for parser in (
            parse_short_score_tolerant,
            parse_adjustment_score,
            parse_score_range_max,
            _parse_negative_range_lower,
        ):
            v = parser(s)
            if v is not None:
                values.add(v)
                break
    return values.pop() if len(values) == 1 else None


def _scope_title_from_page(
    page_text: str,
    header_compact: str,
    *,
    after_total_value: float | None = None,
) -> str | None:
    """定位作用域表头行在起始页文本中的出现位置, 取其上方最近的合格标题行。

 统一策略: 从 start(由 after_total_value 锚点确定, 默认 0)开始扫描所有匹配
 header 出现位置, 各自向上找最近合格标题, 收集候选集后归一唯一性校验:
 - 零候选 → None
 - 所有候选相同 → 返回（多个同构 header 相同标题可接受）
 - 候选不一致 → fail-closed None（同一扫描窗口内不同标题意味歧义）

 after_total_value 用于定位锚点（上一合计行之后才是本作用域有效范围）。
 即使 after_total_value=None, 也必须扫描所有 header——sample26 的"其他评审"
 scope 在独立 chunk 中处理时 after_total_value=None, 但其 header 是页面的
 第二个出现位置, 只看第一个会 fail-closed 造成回归。

 已知边界: 当前 payload 不含 table ordinal(表级序号/坐标)。若扫描窗口内
 仅有一个合格标题候选, 但该标题来自与本 scope 无关的同构表, 则会被误归——
 此情形在"候选唯一"路径下无法识别。多个不同候选时 fail-closed 可拒绝歧义,
 但"单一无关标题"的误归属于现有架构的剩余误差, 需 table ordinal 才能消除。
 """
    if not header_compact:
        return None
    lines = str(page_text or "").splitlines()
    compacts = [_compact(line) for line in lines]
    start = 0
    if after_total_value is not None:
        total_text = _format_score(after_total_value)
        for idx, compact in enumerate(compacts):
            if "合计" in compact and total_text in compact:
                start = idx + 1
                break
    candidates: list[str] = []
    for idx in range(start, len(lines)):
        if compacts[idx] != header_compact:
            continue
        for back in range(idx - 1, start - 1, -1):
            if not compacts[back]:
                continue
            title = _qualify_heading_line(lines[back])
            if title:
                candidates.append(title)
            break  # 首个非空行不合格 → 停止向上找, 继续扫描下一个 header
    unique = set(candidates)
    return unique.pop() if len(unique) == 1 else None


def _qualify_heading_line(line: str) -> str | None:
    """标题行资格: 剥条款号/尾部标点后 2..24 字、>=2 中文、无分值、非汇总类。

 粘连长行处理(I3):
 1. 剥条款号后若含中文句号, 取末段评估;
 2. 末段若为表格结构说明句(_TABLE_DESC_TERMS)或未通过通用验证, 则拒绝末段;
 3. 末段被拒绝时仅回落首段(不取中间段); 首段同样须通过所有门禁。
 4. 两者均失败 → None。
 不得任取正文前段——中间段不参与候选。
 """
    compact = _CLAUSE_PREFIX_RE.sub("", _compact(line)).strip("：:；;、，,。")
    if "。" in compact:
        segments = [s for s in compact.split("。") if s]
        if segments:
            last = segments[-1]
            if not _is_table_description(last):
                validated = _validate_heading_candidate(last)
                if validated:
                    return validated
            # 末段为表结构说明或验证失败 → 仅回落首段
            if len(segments) > 1:
                return _validate_heading_candidate(segments[0])
            return None
    return _validate_heading_candidate(compact)


def _is_table_description(segment: str) -> bool:
    """判断句段是否为表格结构说明句(含'如下表/评审因素及标准/表所示'等)。

    这类句子描述表格引用关系, 不是章节组标题, 须被拒绝以回落首段。
    """
    return any(term in segment for term in _TABLE_DESC_TERMS)


def _validate_heading_candidate(candidate: str) -> str | None:
    """标题候选串验证: 2..24 字、>=2 中文、无分值、非汇总类。"""
    if not (2 <= len(candidate) <= _MAX_TITLE_LEN):
        return None
    if _SCORE_MENTION_RE.search(candidate) or is_total_like_label(candidate):
        return None
    if len(_CHINESE_RE.findall(candidate)) < 2:
        return None
    return candidate


def _parse_negative_range_lower(cell: str) -> float | None:
    """解析含负下界的区间格, 如 '-10～2' / '-10~2' → -10.0。

 复用 parse_adjustment_score 作为负下界验证单源, 不自写负数正则。
 仅在下界为负/零时返回, 纯正区间已由 parse_score_range_max 处理。
 """
    compact = _compact(cell).replace("−", "-")
    for sep in ("～", "~"):
        if sep in compact:
            parts = compact.split(sep, 1)
            lower = parse_adjustment_score(parts[0].strip())
            if lower is not None:
                return lower
    return None


def _compact(text: str) -> str:
    return _WHITESPACE_RE.sub("", str(text or ""))


def _cell(row: list[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _format_score(value: float) -> str:
    rounded = round(float(value), 4)
    if rounded.is_integer():
        return str(int(rounded))
    return str(rounded).rstrip("0").rstrip(".")


__all__ = [
    "apply_section_title_scope_label",
    "attach_scope_rows_to_payload",
    "resolve_table_scopes",
    "scope_labels_for_data_rows",
    "section_title_scope_label",
]

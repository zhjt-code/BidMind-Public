# -*- coding: utf-8 -*-
"""评分表逐行原文组归属恢复（建立, 扩充）。

职责: 从表格网格的**结构位置**恢复每个评分行所属的原文组标签——全宽组标题行
 作用域切分、分组列合并单元格向下继承 + 组内项名前缀家族细分、锚点声明
 闭合有界的部分采纳。
不做: 不判 volume 六类枚举、不产评分项、不解析分值、不调用 LLM/DB、不做词面
 语义联想(不含任何"技术/商务/报价"类关键词表)。

为何需要(根因): 既有 `volume_labeling._volume_label_has_signal` 用关键词表判断
组归属, `clean_volume_label` 又对 30 字以上单元格直接放弃。项目2475 的原文大类
"承包人建议书和承包人实施方案""资信业绩评分标准"不含任何表内关键词, 项目2476 的
"技术标(汇总规则:当专家数量小于等于4位…)" 长达 60+ 字, 两者因此全部静默漏失,
账本侧表现为"已提取100分, 100分未归入任何评分卷"。

判据全部来自结构, 不依赖措辞:
1. 组标题行作用域 = 全宽单格行"X（总分：N分）/X（N分）"把表切成
 连续作用域, **每个作用域的计分行合计都必须与标题声明 N 闭合**才整体采纳
 (项目2478 的"价格评分（总分：15分）/技术评分（53）/商务评分（32）");
2. 分组列 = 名称列**以外**首个"取值稀疏且向下继承后能把行切成 >=2 个多行
 区间"的列; 纯条款号/纯数字列因归一后无实义中文而被排除; 名称列自身及
 "标签==成员行名字族"的列被排除(: 项目2480 的消歧名字族
 "企业规模(2)"曾被误判为分组); 锚点格尾部自带"（N分）"声明时逐组闭合
 校验, 任一组不闭合即整列拒绝全覆盖采纳;
3. 闭合有界部分采纳 = 锚点格自带声明值且合并单元格部分丢失时
 (项目2481 "项目管理大纲评分标准（30分）"格被 pdfplumber 丢弃), 自锚点行
 起累计计分行分值恰好到达声明值即封组, 其后无锚点的行诚实留空——每个
 锚定组都必须精确闭合、闭合组 >=2, 否则整体放弃;
4. 采纳门禁 fail-closed: 全覆盖路径要求全部计分行拿到标签且组数 >=2、组数 <
 计分行数; 任一不成立回退下一判据, 全部落空返回空映射(宁可无标签, 不可错标)。
可选的 `declared_scores` 提供声明侧组基准时追加闭合校验: 恢复出的组分值必须与
声明一致, 否则整体拒收——结构判据负责"谁和谁一组", 声明负责"这一组对不对得上"。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.extractor.scoring.ledger.label_normalization import (
    is_total_like_label,
    normalize_group_label,
)

# 项名前缀分隔符(全角/半角破折号族)。冒号不入列: 规则正文里冒号过于常见。
_PREFIX_SPLIT_RE = re.compile(r"\s*(?:——|—|--|－－|–)\s*")
# 单元格尾部的括注说明与兄弟消歧序号: "技术标(汇总规则:…)" / "资信标(2)"。
_PAREN_TAIL_RE = re.compile(r"[（(][^（()）]*[)）]\s*$")
# 单元格首部的括注标注: "（明标）资信业绩评分标准"(评审形式修饰, 非标签实义)。
_PAREN_HEAD_RE = re.compile(r"^[（(][^（()）]*[)）]")
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
_SCORE_TAIL_RE = re.compile(r"\s*\d+(?:\.\d+)?\s*分?\s*$")
# 锚点格尾部自带的组声明值: "资信业绩评分标准（3分）" -> 3。
# 必须锚定尾部——"技术标(汇总规则:…0.5分…)"这类规则括注不是组声明值。
_ANCHOR_DECLARED_TAIL_RE = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]$")
# 组标题行: 全宽单格 "价格评分（总分：15分）" / "商务评分（32分）"。
_GROUP_HEADER_ROW_RE = re.compile(
    r"^(?P<label>[^（()）]{2,24}?)\s*[（(]\s*(?:总分|满分)?\s*[:：]?\s*"
    r"(?P<score>\d+(?:\.\d+)?)\s*分\s*[）)]$"
)
_MAX_LABEL_LEN = 24
_MIN_LABEL_CJK = 2
_MIN_GROUPS = 2
_CLOSURE_TOLERANCE = 0.01


def recover_row_group_labels(
    rows: list[list[str]],
    *,
    name_col: int | None = None,
    score_col: int | None = None,
    row_scores: dict[int, float] | None = None,
    row_names: dict[int, str] | None = None,
    declared_scores: dict[str, float] | None = None,
    leading_rows: list[list[str]] | None = None,
) -> dict[int, str]:
    """恢复逐行原文组标签; 任一门禁不成立返回空映射(调用方回退既有逻辑)。

 rows: 数据行(不含表头)。计分行与项名有两种取法, 覆盖两条解析路径:
 - 有独立分值列的明细表: 传 score_col / name_col;
 - 分值嵌在项名里的表(如"XX（1分）"): 传调用方已解析出的 row_scores / row_names。
 declared_scores: 声明侧组基准(归一键 -> 分值), 提供时追加组闭合校验。
 leading_rows: 位于 rows 之前、被表头选择消费掉的行(: 首个全宽组标题
 行"价格评分（总分：15分）"常被当作表头拿走), 仅供判据1识别首作用域。
 返回映射可能只覆盖部分计分行(判据3 的闭合有界采纳), 未覆盖行保持无标签。
 """
    if not rows:
        return {}
    scored = _scored_rows(rows, score_col, row_scores)
    if len(scored) < _MIN_GROUPS:
        return {}
    score_of = _make_score_lookup(rows, score_col, row_scores)

    labels = _labels_from_group_header_rows(rows, scored, score_of, leading_rows)
    if not labels:
        labels = _labels_from_group_column(
            rows, scored, name_col=name_col, row_names=row_names, score_of=score_of
        )
        if labels:
            labels = _refine_by_name_prefix(
                rows, scored, labels, name_col=name_col, row_names=row_names
            )
    if not labels:
        labels = _closure_bounded_anchor_labels(
            rows, scored, name_col=name_col, row_names=row_names, score_of=score_of
        )
    if not labels:
        return {}
    if not _closure_holds(labels, score_of, declared_scores):
        return {}
    return labels


def backfill_recovered_group_labels(
    data: list[list[str]],
    built_rows: list[tuple[int, Any]],
    *,
    leading_rows: list[list[str]] | None = None,
) -> None:
    """按已解析评分项(行号, item)回填组标签（嵌入式路径模式的共享版）。

 只覆盖尚无标签的项——已有权威标签不动; 恢复标签属弱证据(未必经父子分值闭合
 验证), 与词面路径同样要过细分, 否则"其他因素"这类粗标签会顶替按项名细分出
 的业绩/信誉(已建立的语义)。
 """
    from app.core.extractor.scoring.volume_labeling import refine_scoring_group_label

    if len(built_rows) < 2:
        return
    recovered = recover_row_group_labels(
        data,
        row_scores={
            idx: float(getattr(item, "score_weight", 0) or 0.0)
            for idx, item in built_rows
        },
        row_names={
            idx: str(getattr(item, "score_item_name", "") or "")
            for idx, item in built_rows
        },
        leading_rows=leading_rows,
    )
    for row_idx, item in built_rows:
        label = recovered.get(row_idx)
        if not label or getattr(item, "volume_label", None):
            continue
        item.volume_label = refine_scoring_group_label(label, item.score_item_name)


def _scored_rows(
    rows: list[list[str]], score_col: int | None, row_scores: dict[int, float] | None
) -> list[int]:
    """计分行下标: 调用方给出的解析结果优先, 否则按分值列扫描。"""
    if row_scores is not None:
        return sorted(row_scores)
    if score_col is None:
        return []
    return [idx for idx, row in enumerate(rows) if _score_of(row, score_col) is not None]


def _make_score_lookup(
    rows: list[list[str]], score_col: int | None, row_scores: dict[int, float] | None
):
    """行号 -> 分值 的统一取值闭包(两条调用路径共用)。"""

    def lookup(idx: int) -> float:
        if row_scores is not None:
            return float(row_scores.get(idx) or 0.0)
        return float(_score_of(rows[idx], score_col) or 0.0)

    return lookup


# ---------------------------------------------------------------------------
# 判据1: 全宽组标题行作用域
# ---------------------------------------------------------------------------


def _labels_from_group_header_rows(
    rows: list[list[str]],
    scored: list[int],
    score_of,
    leading_rows: list[list[str]] | None = None,
) -> dict[int, str]:
    """全宽组标题行切分作用域; 每个作用域必须与标题声明分值闭合才整体采纳。

 leading_rows 中的组标题行以虚拟行号 -1 参与(: 首个组标题行常被
 `_select_header_and_data` 当作表头消费, 不在 rows 内)。
 """
    headers: list[tuple[int, str, float]] = []
    for row in leading_rows or []:
        parsed = _parse_group_header_row(row)
        if parsed is not None:
            headers.append((-1, parsed[0], parsed[1]))
            break  # 前导行至多贡献一个首作用域标题
    for idx, row in enumerate(rows):
        parsed = _parse_group_header_row(row)
        if parsed is not None:
            headers.append((idx, parsed[0], parsed[1]))
    if len(headers) < _MIN_GROUPS:
        return {}
    if any(s < headers[0][0] for s in scored):
        return {}  # 首个标题行之前存在计分行 -> 作用域不完整, 放弃
    labels: dict[int, str] = {}
    for pos, (start, label, declared) in enumerate(headers):
        end = headers[pos + 1][0] if pos + 1 < len(headers) else len(rows)
        members = [s for s in scored if start < s < end]
        total = round(sum(score_of(s) for s in members), 4)
        if abs(total - declared) > _CLOSURE_TOLERANCE:
            return {}  # 任一作用域不闭合 -> 整表拒绝(fail-closed)
        for member in members:
            labels[member] = label
    if len(labels) != len(scored):
        return {}
    return labels


def _parse_group_header_row(row: list[str]) -> tuple[str, float] | None:
    """行是否为全宽组标题行"X（总分：N分）"; 是则返回 (标签, 声明分值)。"""
    non_empty = [str(cell or "").strip() for cell in row if str(cell or "").strip()]
    if len(non_empty) != 1:
        return None
    compact = re.sub(r"\s+", "", non_empty[0])
    match = _GROUP_HEADER_ROW_RE.match(compact)
    if not match:
        return None
    label = _normalize_label(match.group("label"))
    if not label:
        return None
    try:
        score = float(match.group("score"))
    except (TypeError, ValueError):
        return None
    return (label, score) if score > 0 else None


# ---------------------------------------------------------------------------
# 判据2: 分组列合并单元格向下继承 + 名字族/锚点闭合守卫
# ---------------------------------------------------------------------------


def _labels_from_group_column(
    rows: list[list[str]],
    scored: list[int],
    *,
    name_col: int | None,
    row_names: dict[int, str] | None,
    score_of,
) -> dict[int, str]:
    """自左向右取首个能把计分行切成 >=2 个组的分组列, 合并单元格向下继承。

 名称列自身不参与候选(: 名称列的消歧名字族"企业规模(2)"会伪装成
 "组数<行数"的合法分区), 且只扫名称列**左侧**——右侧的说明/备注列可能出现
 重复短语形成伪分区; 锚点格尾部自带"（N分）"声明时逐组闭合校验。
 """
    for column in _candidate_columns(rows, name_col):
        labels = _inherit_column_labels(rows, scored, column)
        if not _partition_is_usable(labels, scored):
            continue
        if _labels_shadow_row_names(rows, scored, labels, name_col, row_names):
            continue
        if not _anchor_declared_closure_holds(rows, scored, labels, column, score_of):
            continue
        return labels
    return {}


def _inherit_column_labels(
    rows: list[list[str]], scored: list[int], column: int
) -> dict[int, str]:
    """单列向下继承: 非空格开启新组, 空格沿用上一组(合并单元格的文本投影形态)。"""
    labels: dict[int, str] = {}
    scored_set = set(scored)
    current = ""
    for idx, row in enumerate(rows):
        raw = _cell(row, column)
        if raw:
            normalized = _normalize_label(raw)
            if not normalized:
                # 该列出现无法作标签的实义文本(规则正文/长描述) -> 整列不可用。
                return {}
            current = normalized
        if idx in scored_set and current:
            labels[idx] = current
    return labels


def _partition_is_usable(labels: dict[int, str], scored: list[int]) -> bool:
    """分区可用性: 全部计分行有标签、组数 >=2 且严格粗于行(否则等于逐行造组)。"""
    if len(labels) != len(scored):
        return False
    distinct = set(labels.values())
    return _MIN_GROUPS <= len(distinct) < len(scored)


def _labels_shadow_row_names(
    rows: list[list[str]],
    scored: list[int],
    labels: dict[int, str],
    name_col: int | None,
    row_names: dict[int, str] | None,
) -> bool:
    """分区是否只是项名列的影子: 已标注行的标签全部等于自身名字族。

 "企业规模/企业规模(2)/企业规模(3)"这类兄弟消歧名归一后同族, 会让名称列
 伪装成"组数<行数"的分组列; 真实分组列的标签与成员行项名普遍不同。
 只看**已标注行**——部分覆盖时以未覆盖行判"非影子"会放行整片名字族假标签
 (2478 实测: "报价得分"因含"得分"落出标签集, 曾使全表名字族标签逃过守卫)。
 """
    if name_col is None and row_names is None:
        return False
    checked = False
    for idx, label in labels.items():
        source = (
            row_names.get(idx, "") if row_names is not None else _cell(rows[idx], name_col)
        )
        family = _normalize_label(source)
        if not family or label != family:
            return False
        checked = True
    return checked


def _anchor_declared_closure_holds(
    rows: list[list[str]],
    scored: list[int],
    labels: dict[int, str],
    column: int,
    score_of,
) -> bool:
    """锚点格尾部自带"（N分）"声明时, 逐锚定组做闭合校验(守卫)。

 合并单元格部分丢失(2481 大纲格)会让上方锚点无界继承到不属于它的行,
 此时该锚定组的行合计必然超出自身声明值, 整列拒绝全覆盖采纳。
 无声明值的锚点(2476 "技术标(汇总规则:…)")不受此校验约束。
 """
    anchors = _column_anchor_rows(rows, column)
    if not anchors:
        return True
    group_totals: dict[int, float] = {}
    for idx in scored:
        anchor_row = max((a for a, _l, _d in anchors if a <= idx), default=None)
        if anchor_row is None:
            continue
        group_totals[anchor_row] = round(group_totals.get(anchor_row, 0.0) + score_of(idx), 4)
    for anchor_row, _label, declared in anchors:
        if declared is None:
            continue
        if abs(group_totals.get(anchor_row, 0.0) - declared) > _CLOSURE_TOLERANCE:
            return False
    return True


def _column_anchor_rows(
    rows: list[list[str]], column: int
) -> list[tuple[int, str, float | None]]:
    """列内锚点行: (行号, 标签, 尾部声明值或 None)。"""
    anchors: list[tuple[int, str, float | None]] = []
    for idx, row in enumerate(rows):
        raw = _cell(row, column)
        if not raw:
            continue
        label = _normalize_label(raw)
        if not label:
            continue
        anchors.append((idx, label, _anchor_declared_score(raw)))
    return anchors


def _anchor_declared_score(raw: str) -> float | None:
    """锚点格尾部的组声明值"…（3分）"; 非尾部/非法返回 None。"""
    compact = re.sub(r"\s+", "", str(raw or ""))
    match = _ANCHOR_DECLARED_TAIL_RE.search(compact)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# ---------------------------------------------------------------------------
# 判据3: 锚点声明闭合有界的部分采纳
# ---------------------------------------------------------------------------


def _closure_bounded_anchor_labels(
    rows: list[list[str]],
    scored: list[int],
    *,
    name_col: int | None,
    row_names: dict[int, str] | None,
    score_of,
) -> dict[int, str]:
    """锚点自带声明值时按"累计恰好闭合"封组, 无锚点覆盖的行诚实留空。

    适用形态(2481): 分组列的合并单元格部分丢失, 全覆盖继承必然把孤行错并进
    上方锚点组。此路径要求**每个带声明值的锚点组都精确闭合**且闭合组 >=2,
    任何一组无法由锚点行起的连续计分行恰好求和到声明值, 整体放弃。
    """
    for column in _candidate_columns(rows, name_col):
        anchors = [a for a in _column_anchor_rows(rows, column) if a[2] is not None]
        # 锚点必须严格少于计分行——组是行的粗化; "每行都是锚点"的列(嵌入式
        # 名称列"XX（N分）"逐行自闭合)不是分组列。
        if not (_MIN_GROUPS <= len(anchors) < len(scored)):
            continue
        labels = _bind_anchors_by_closure(anchors, scored, score_of)
        if not labels or len(set(labels.values())) < _MIN_GROUPS:
            continue
        if _labels_shadow_row_names(rows, scored, labels, name_col, row_names):
            continue
        return labels
    return {}


def _candidate_columns(rows: list[list[str]], name_col: int | None) -> range:
    """分组列候选范围: 名称列左侧; 名称列未知时全列(嵌入式路径)。"""
    last_col = max(len(row) for row in rows) - 1
    return range(0, name_col if name_col is not None else last_col + 1)


def _bind_anchors_by_closure(
    anchors: list[tuple[int, str, float | None]], scored: list[int], score_of
) -> dict[int, str]:
    """自每个锚点行起, 连续计分行累计到声明值即封组; 任一锚点组不闭合返回空。"""
    labels: dict[int, str] = {}
    anchor_rows = [a for a, _l, _d in anchors]
    for pos, (start, label, declared) in enumerate(anchors):
        boundary = anchor_rows[pos + 1] if pos + 1 < len(anchor_rows) else None
        running = 0.0
        closed = False
        for idx in scored:
            if idx < start:
                continue
            if boundary is not None and idx >= boundary:
                break
            running = round(running + score_of(idx), 4)
            labels[idx] = label
            if abs(running - float(declared or 0.0)) <= _CLOSURE_TOLERANCE:
                closed = True
                break
            if running > float(declared or 0.0) + _CLOSURE_TOLERANCE:
                return {}
        if not closed:
            return {}
    return labels


# ---------------------------------------------------------------------------
# 判据2细分 + 收尾校验 + 单元格工具
# ---------------------------------------------------------------------------


def _refine_by_name_prefix(
    rows: list[list[str]],
    scored: list[int],
    labels: dict[int, str],
    *,
    name_col: int | None,
    row_names: dict[int, str] | None = None,
) -> dict[int, str]:
    """组内项名带同一分隔符前缀且前缀 >=2 种时, 用前缀细分该组。

    2475 形态: 分组列合并单元格写作"承包人建议书和承包人实施方案", 而项名逐行带
    "承包人建议书——X"/"承包人实施方案——X" 前缀, 真实组基准是拆开的 17/13 两组。
    要求组内**全部**计分行都带前缀, 避免用个别行的措辞拆散整组。
    """
    if name_col is None and row_names is None:
        return labels
    by_group: dict[str, list[int]] = {}
    for idx in scored:
        by_group.setdefault(labels[idx], []).append(idx)

    refined = dict(labels)
    for group, indices in by_group.items():
        prefixes: dict[int, str] = {}
        for idx in indices:
            source = (
                row_names.get(idx, "")
                if row_names is not None
                else _cell(rows[idx], name_col)
            )
            prefix = _name_prefix(source)
            if not prefix or prefix == group:
                prefixes = {}
                break
            prefixes[idx] = prefix
        if len(set(prefixes.values())) < _MIN_GROUPS:
            continue
        refined.update(prefixes)
    return refined


def _closure_holds(
    labels: dict[int, str], score_of, declared_scores: dict[str, float] | None
) -> bool:
    """调用方提供声明侧基准时, 恢复出的每个组分值必须与声明闭合。"""
    if not declared_scores:
        return True
    totals: dict[str, float] = {}
    for idx, label in labels.items():
        key = normalize_group_label(label)
        totals[key] = round(totals.get(key, 0.0) + score_of(idx), 4)
    for key, total in totals.items():
        declared = declared_scores.get(key)
        if declared is None or abs(declared - total) > _CLOSURE_TOLERANCE:
            return False
    return True


def _normalize_label(raw: str) -> str:
    """单元格文本 -> 组标签; 无实义中文/超长/汇总类一律判不可用(返回空串)。"""
    text = re.sub(r"\s+", "", str(raw or ""))
    text = _SCORE_TAIL_RE.sub("", text)
    while True:
        stripped = _PAREN_TAIL_RE.sub("", text)
        head = _PAREN_HEAD_RE.match(stripped)
        if head is not None and head.end() < len(stripped):
            stripped = stripped[head.end():]
        if stripped == text:
            break
        text = stripped
    text = text.strip(" 　:：、,，。;；")
    if not text or len(text) > _MAX_LABEL_LEN:
        return ""
    if len(_CHINESE_RE.findall(text)) < _MIN_LABEL_CJK:
        # 条款号/序号列("2.2.4（1）和（3）")归一后无实义中文, 不是组标签。
        return ""
    if is_total_like_label(text):
        return ""
    return text


def _name_prefix(raw: str) -> str:
    """项名分隔符前缀; 无分隔符或前缀不可用时返回空串。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    parts = _PREFIX_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return ""
    return _normalize_label(parts[0])


def _score_of(row: list[str], score_col: int | None) -> float | None:
    """分值格解析(仅严格短分值, 与解析器口径一致, 不扫规则正文)。"""
    from app.core.extractor.scoring.score_column_semantics import parse_short_score_tolerant

    return parse_short_score_tolerant(_cell(row, score_col))


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index < 0 or index >= len(row):
        return ""
    return str(row[index] or "").strip()


__all__ = ["backfill_recovered_group_labels", "recover_row_group_labels"]

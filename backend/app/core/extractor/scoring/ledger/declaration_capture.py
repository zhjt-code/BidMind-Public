"""评分声明捕获（换轴重构 P1 R1，探针 v1 捕获口径产品化）。

职责: 从页文本与表格网格确定性捕获原文组声明对与显式总分候选——组锚点门控版、
 volume_parser 第一阶段原文对（平铺/内联/每部分满分/折算卷定义）、声明总分句、
 扩展形态（括注式/组尾缀裸数字）、表内触发行总分候选与同行权重附加；
 按(归一标签,分值)去重，并按 对同标签冲突声明整组拒用(fail-visible)。
不做: 不做枚举卷映射、不对账、不裁决项归属、不调用 LLM/DB、不读文件、
 不修改既有解析器行为。

与探针 v1 的口径差异（收紧项）:
1. 同标签冲突: 探针保留双份, 本模块按 整组拒用并写入 conflicts;
2. 负向门控: 以"标准"结尾但非组语义标准形态(评分/评审/评标标准)的
 扩展形态候选视为单项自身分值上限, 拒收并计入诊断;
3. 无冒号构成列举("分值构成: 商务20分")为探针口径外补充, 带
 名词触发+同句扫描+至少2对的三重门控。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from app.core.extractor.scoring import volume_parser as _vp  # noqa: PLC2701 私有正则只读复用(探针先例)
from app.core.extractor.scoring.group_column import (
    GroupColumnDetection,
    detect_group_column,
)
from app.core.extractor.scoring.ledger.contracts import (
    SCORE_MATCH_TOLERANCE,
    DeclarationPair,
    LedgerCapture,
)
from app.core.extractor.scoring.ledger.label_normalization import (
    is_total_like_label,
    normalize_group_label,
)
from app.core.extractor.scoring.score_column_semantics import parse_short_score_tolerant
from app.core.parser.scoring_group_anchor import extract_closed_group_anchors_from_pages

# 声明总分句: "总分100分/满分100分/合计100分/总计100分"（探针裁决口径）。
# 「得满分N分」是项级满分句(如「相等者得满分50分」), 不得升格为文档总分。
_TOTAL_SENTENCE_RE = re.compile(
    r"(?:总分|(?<!得)满分|合计|总计)\s*[共为]?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*分"
)
# 折算卷定义标签: "技术总分100分...按40%权重折算"（权重由 volume_parser 正则确认）。
_WEIGHTED_DEF_LABEL_RE = re.compile(
    r"([\u4e00-\u9fff]{1,10}?)总分\s*[:：]?\s*(\d+(?:\.\d+)?)\s*分"
)
# 括注式组声明: 整行/整格 "技术商务部分（70分）" / "资信标 （100 分）"。
_GROUP_PAREN_RE = re.compile(
    r"^(?:[一二三四五六七八九十\d]+[、.．]\s*)?([\u4e00-\u9fff]{2,12})\s*[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[)）]$"
)
# 组尾缀裸数字式: 整行/整格 "一、价格部分 20" / "商务标 34 分"。
_GROUP_BARE_RE = re.compile(
    r"^(?:[一二三四五六七八九十\d]+[、.．]\s*)?([\u4e00-\u9fff]{2,10})\s+(\d+(?:\.\d+)?)\s*(分)?$"
)
# 组尾缀门控: 扩展形态仅收组形标签（防"质量保证措施（15分）"等项级行入组对）。
_GROUP_SUFFIX_GATE = ("部分", "标", "卷", "评审", "标准", "项", "书")
# 收紧: 以"标准"结尾的标签仅当为组语义标准形态才收,
# 排除"项目负责人配备标准(7分)"这类单个评分项自身名称括号内的分值上限。
_STANDARD_GROUP_FORMS = ("评分标准", "评审标准", "评标标准")
_WEIGHT_CELL_RE = re.compile(r"^\d+(?:\.\d+)?\s*[%％]$")
_NUMERIC_CELL_RE = re.compile(r"^\d+(?:\.\d+)?$")
# 无冒号构成列举: "分值构成: 商务20分；技术30分"（名词触发+同句+>=2对门控）。
_LISTING_TRIGGER_RE = re.compile(r"(?:分值构成|分值分配|分值组成)\s*[:：]?")
_LISTING_PAIR_RE = re.compile(r"([\u4e00-\u9fff]{2,8}?)\s*(\d+(?:\.\d+)?)\s*分")
_LISTING_END_RE = re.compile(r"[。\n]")
# 权重构成"X占N分/X：N分"列举(doc2478 实测为全角冒号形态:
# "磋商报价部分F1：20分、技术、商务部分F2：80分")。标签允许中缀顿号与尾部
# 短编号标记(F1/F2), 惰性扩展至连接符(占/冒号)。
_OCCUPY_PAIR_RE = re.compile(
    r"([\u4e00-\u9fff][\u4e00-\u9fff、，,]{0,12}?(?:[A-Za-z]\d{0,2})?)"
    r"(?:占|[:：])\s*(\d+(?:\.\d+)?)\s*分"
)
# 账本专用平铺配对：支持原文标签内连接符 `-－—_` 及字母数字。
# 差异于 _vp._FLAT_PAIR_PATTERN：
# 1. 增加左边界单字符断言——防止前文汉字链被 {2,12} 截断为无意义短标签
#    （超长连续汉字链无合法起点则整体不匹配，宁可不出对也不出垃圾标签）；
# 2. 标签字符集扩展：允许连接符后接可选字母数字再接汉字（每段≤12字）；
# 3. 最多 2 段连接符扩展（过多连接符通常不是组名）；
# 4. 连接符后的字母数字不能单独收尾，须由至少 1 个汉字确认其仍是业务标签。
# 注：行首触发由调用方的哨兵空格实现，正则本身无 ^ 分支。
_LEDGER_FLAT_PAIR_PATTERN = re.compile(
    r"(?<=[分\s:：,，、;；()\[\]])"
    r"([\u4e00-\u9fff]{2,12}(?:[-－—_][a-zA-Z0-9]*[\u4e00-\u9fff]{1,12}){0,2})"
    r"(?:[（(][^）)]{0,24}[）)])?"
    r"[\s:：]*[:：]\s*"
    r"(\d+(?:\.\d+)?)\s*分(?![值配组])"
)
# 表格非触发窗口行仍保留的第一阶段来源（强特征, 探针口径）。
_NON_SCAN_KEEP_SOURCES = ("weighted_def", "inline")
# 组锚点闭合门控的期望总分（探针实测口径: 组满分合计闭合到100才入强集）。
_ANCHOR_GATE_EXPECTED_TOTAL = 100.0


def capture_scoring_declarations(
    pages: list[tuple[int, str]],
    table_grids: list[tuple[int, list[list[str]]]],
    *,
    supplementary_pages: list[tuple[int, str]] | None = None,
    parallel_scope_labels: list[str] | None = None,
) -> LedgerCapture:
    """捕获原文组声明对与显式总分候选, 产出去重+冲突拒用后的 LedgerCapture。

 pages: (页码, 页文本)；table_grids: (页码, 二维单元格文本)。
 supplementary_pages: 文档级声明补充文本(见 `declaration_pages_from_signals`)。
 它**只走并列卷权重声明这一条强门禁通路**, 既不贡献总分候选, 也不走平铺/内联/
 扩展形态。两条收紧各有实测反例:
 - 总分候选: 正文里"投标报价满分30分"这类项级上限与真实文档总分在文本层无法
 可靠区分, 直接采信会产出高置信假缺口(原型, 据此列为遗留);
 - 平铺组对: 正文按页拼接后表头词会粘进标签(项目2475 实测产出
 "编列内容承包人建议书:17分"), 该伪组在提取侧永远无对应, 表现为凭空 -17 缺口。
 并列卷权重声明自带序位等长 + 权重和≈100 + 枚举完备三重 fail-closed 门禁,
 跨片段误命中需要多重巧合, 故只放开这一侧。
 """
    raw_pairs: list[dict] = []
    totals: list[float] = []
    exclusions: list[str] = []

    # 锚点声明句"价格评分(总分:15分)"里的"总分:15"是**组作用域**总分,
    # 不得升格为文档级总分候选。锚点门禁要求 >=2 个组闭合到 100 且每组 >0,
    # 按构造任一锚点分值必 <100, 故该抑制不可能误杀真实的"总分100分"声明。
    anchor_scores_by_page: dict[int, set[float]] = {}
    for anchor in extract_closed_group_anchors_from_pages(
        pages, expected_total=_ANCHOR_GATE_EXPECTED_TOTAL
    ):
        raw_pairs.append({
            "label": anchor.label, "score": float(anchor.max_score),
            "weight": None, "source": "anchor_gated", "page": anchor.page_num,
        })
        anchor_scores_by_page.setdefault(anchor.page_num, set()).add(
            round(float(anchor.max_score), 4)
        )

    # 结构优先——先按分组列(结构+父子闭合)捕获, 命中的表跳过词面路径,
    # 且其单元格内的"满分N分"不得升格为文档级总分候选(那是组/项自身上限)。
    structural = _structural_grid_captures(table_grids)
    for capture in structural.values():
        raw_pairs.extend(capture.pairs)

    row_level_totals = _row_level_total_values_by_page(table_grids)
    for page_num, text in pages:
        page_pairs, page_totals, page_exclusions = _declaration_pairs_from_page_text(page_num, text)
        raw_pairs.extend(page_pairs)
        totals.extend(
            _drop_in_table_totals(
                page_totals, structural, page_num,
                row_level_values=(
                    row_level_totals.get(page_num, set())
                    | anchor_scores_by_page.get(page_num, set())
                ),
            )
        )
        exclusions.extend(page_exclusions)

    for grid_index, (page_num, grid) in enumerate(table_grids):
        if grid_index in structural:
            continue
        grid_pairs, grid_totals, grid_exclusions = _declaration_pairs_from_table_grid(page_num, grid)
        raw_pairs.extend(grid_pairs)
        totals.extend(grid_totals)
        exclusions.extend(grid_exclusions)

    for page_num, text in supplementary_pages or ():
        raw_pairs.extend(
            {**pair, "page": page_num} for pair in _weighted_share_pairs(text)
        )

    scheme_evidence = _has_scheme_evidence([*pages, *(supplementary_pages or ())])
    parallel_scope_norms = tuple(
        dict.fromkeys(
            norm
            for norm in (
                normalize_group_label(label)
                for label in (parallel_scope_labels or [])
            )
            if norm
        )
    )
    if len(parallel_scope_norms) < 2:
        parallel_scope_norms = ()
    deduped, total_like_skipped = _dedupe_declaration_pairs(raw_pairs)
    kept, conflicts = _split_label_conflicts(deduped)
    pairs = tuple(
        DeclarationPair(
            label=p["label"], norm_label=p["norm_label"], score=p["score"],
            weight=p.get("weight"), source=p["source"], page=p.get("page"),
        )
        for p in kept
    )
    diagnostics = (
        f"ext_suffix_gate_excluded={exclusions.count('suffix_gate')}",
        f"ext_item_standard_excluded={exclusions.count('item_standard')}",
        f"dedup_total_like_skipped={total_like_skipped}",
        f"group_column_tables={len(structural)}",
        f"scheme_evidence={scheme_evidence}",
        f"parallel_scope_groups={len(parallel_scope_norms)}",
    )
    return LedgerCapture(
        pairs=pairs,
        total_candidates=tuple(sorted({round(v, 4) for v in totals})),
        conflicts=tuple(conflicts),
        scheme_evidence=scheme_evidence,
        parallel_scope_norms=parallel_scope_norms,
        diagnostics=diagnostics,
    )


def declaration_pages_from_signals(signals: Any) -> list[tuple[int, str]]:
    """把文档级评分声明快照展开为账本补充文本候选（输入范围缺口）。

 职责: 只做"取出已捕获的声明句/标题文本, 并按页给出可解析的拼接候选"。
 不做: 不解析分值、不判定卷别、不读 DB。

 为何要拼接: 同一句声明常被换行拆进 `declarations` 与 `headings` 两个条目
 (项目2476 实测断在"分别占30%、"之后), 单独喂任一片段都解析不出。而两个列表
 之间的**页内真实先后次序已丢失**, 同页还夹着其他无关条目, 整页直接拼成一段会
 被无关文本隔断。故产出"每个片段单独 + 同页片段两两有序拼接"的候选集合——采纳
 与否由 weighted_share_composition 的三重 fail-closed 门禁把关, 此处只负责让
 正确的组合有机会被看到。
 快照缺失/形状异常时返回空列表, 保持确认页主链不受影响。
 """
    by_page: dict[int, list[str]] = {}
    for field_name in ("declarations", "headings"):
        entries = (signals or {}).get(field_name) if isinstance(signals, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            try:
                page = int(entry.get("page") or 0)
            except (TypeError, ValueError):
                continue
            by_page.setdefault(page, []).append(text)

    out: list[tuple[int, str]] = []
    for page, texts in sorted(by_page.items()):
        out.extend((page, text) for text in texts)
        out.extend(
            (page, f"{left} {right}") for left, right in combinations(texts, 2)
        )
    return out


def declaration_grids_from_signals(signals: Any) -> list[tuple[int, list[list[str]]]]:
    """把快照里的声明样表行展开为 capture 可消费的精简网格。

    职责: 只做"取出已逐字捕获的声明表行, 转成 (page, grid) 形态"。
    不做: 不解析分值、不判定卷别、不读 DB。

    为何必须走 table_grids 而非 supplementary_pages: 后者只跑 `_weighted_share_pairs`,
    不跑平铺/括注解析; 而这类声明的形态是"标题格 + 内容格"两格一行, 必须由
    `_declaration_pairs_from_table_grid` 拼行后交第一阶段解析才能逐对取出
    (实测按补充文本喂入时 4 个受益样本全部挽回失败, 按精简网格喂入则全部命中)。

    每行独立成一张单行网格: 行间本就无结构关系, 合成多行网格反而会让
    列角色推断把不相干的行当作同表数据。快照缺失/形状异常时返回空列表。
    """
    entries = (signals or {}).get("declaration_table_rows") if isinstance(signals, dict) else None
    if not isinstance(entries, list):
        return []

    grids: list[tuple[int, list[list[str]]]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cells = entry.get("cells")
        if not isinstance(cells, list) or not any(str(c or "").strip() for c in cells):
            continue
        try:
            page = int(entry.get("page") or 0)
        except (TypeError, ValueError):
            continue
        grids.append((page, [[str(c or "") for c in cells]]))
    return grids


def _has_scheme_evidence(pages: list[tuple[int, str]]) -> bool:
    """文档是否存在"多套备选评分方案"的独立证据。

 复用装配层既有的结构判据 `has_multi_template_scheme_signal`(模板编码锚点>=2
 + 模板/细则语境,), 不新增任何卷名/方案名词表。判据不成立时对账器
 拒绝做方案身份断言——宁可漏报罕见的真多方案, 也不把常见的并行评分卷误报成
 多方案(误报会否定正确结果并引导用户去改本就正确的数据)。
 """
    from app.core.parser.scoring_chunk_sources import has_multi_template_scheme_signal

    return has_multi_template_scheme_signal([text for _page, text in pages])


@dataclass(frozen=True)
class _StructuralGridCapture:
    """单表分组列捕获产物: 组声明对 + 该表内部的"满分N分"值(不得作文档总分)。"""

    detection: GroupColumnDetection
    pairs: tuple[dict, ...]
    inner_total_values: frozenset[float]


def _structural_grid_captures(
    table_grids: list[tuple[int, list[list[str]]]],
) -> dict[int, _StructuralGridCapture]:
    """按 grid 序号返回分组列命中结果; 未命中的表不入字典(调用方回退词面路径)。"""
    captures: dict[int, _StructuralGridCapture] = {}
    for grid_index, (page_num, grid) in enumerate(table_grids):
        detection = detect_group_column(grid)
        if detection is None:
            continue
        pairs = tuple(
            {
                "label": group.label, "score": group.score, "weight": None,
                "source": "group_column", "page": page_num,
            }
            for group in detection.groups
        )
        captures[grid_index] = _StructuralGridCapture(
            detection=detection,
            pairs=pairs,
            inner_total_values=frozenset(_inner_total_values(grid)),
        )
    return captures


def _inner_total_values(grid: list[list[str]]) -> set[float]:
    """表内单元格出现的"满分/总分N分"数值集合。

    分组列已确定性给出每组分值, 故该表内任何"满分N分"按构造只能是组/项自身上限
    (doc1933 的"投标报价满分30分"即项级规则文本), 不得作为文档级总分基准。
    """
    values: set[float] = set()
    for row in grid or []:
        for cell in row or []:
            for raw in _TOTAL_SENTENCE_RE.findall(str(cell or "")):
                try:
                    values.add(round(float(raw), 4))
                except ValueError:
                    continue
    return values


def _row_level_total_values_by_page(
    table_grids: list[tuple[int, list[list[str]]]],
) -> dict[int, set[float]]:
    """逐页收集行级"满分N分"值: 同行存在独立分值格且数值≈N 即判行级上限。

 抑制的泛化——不再依赖分组列命中: "价格项(F1×A1)满分为10.0000分"这类
 组/项自身满分句与其所在行的分值格(如"10分")数值必然相等, 而真实的文档级总分
 声明句不会与恰等于自身数值的行分值格同行。判据为结构+数值, 非词面白名单;
 误抑真实总分的代价是诚实降级(L4, fail-visible), 不会产出高置信错基准
 (承 "宁可低置信不可错基准"方向)。
 """
    by_page: dict[int, set[float]] = {}
    for page_num, grid in table_grids:
        for row in grid or []:
            cells = [str(cell or "") for cell in (row or [])]
            row_scores = {
                round(score, 4)
                for score in (parse_short_score_tolerant(cell) for cell in cells)
                if score is not None
            }
            if not row_scores:
                continue
            for cell in cells:
                for raw in _TOTAL_SENTENCE_RE.findall(cell):
                    try:
                        value = round(float(raw), 4)
                    except ValueError:
                        continue
                    if any(abs(value - score) <= SCORE_MATCH_TOLERANCE for score in row_scores):
                        by_page.setdefault(page_num, set()).add(value)
    return by_page


def _drop_in_table_totals(
    page_totals: list[float],
    structural: dict[int, _StructuralGridCapture],
    page_num: int,
    *,
    row_level_values: frozenset[float] | set[float] = frozenset(),
) -> list[float]:
    """剔除同页结构化表内部与行级的组/项级"满分N分", 保留表外真实总分声明句。"""
    suppressed: set[float] = set(row_level_values)
    for capture in structural.values():
        if any(pair["page"] == page_num for pair in capture.pairs):
            suppressed |= capture.inner_total_values
    if not suppressed:
        return page_totals
    return [value for value in page_totals if round(value, 4) not in suppressed]


def _declaration_pairs_from_page_text(
    page_num: int, text: str
) -> tuple[list[dict], list[float], list[str]]:
    """单页文本捕获: 声明总分句 + 触发词行窗口(前1后2行)第一阶段对 + 扩展形态整行匹配。"""
    pairs: list[dict] = []
    totals: list[float] = [float(v) for v in _TOTAL_SENTENCE_RE.findall(str(text or ""))]
    exclusions: list[str] = []
    # 并列卷权重声明("满分均为100…分别占30%、15%、55%的权重")常被换行
    # 拆散(2476 实测断在"分别占30%、"之后), 逐行扫描必漏; 且它没有"分值构成"这类
    # 触发词, 走不进下面的触发行窗口。故先在整页拼平文本上单独跑一次。
    pairs.extend(
        {**pair, "page": page_num}
        for pair in _weighted_share_pairs(" ".join(str(text or "").split()))
    )
    lines = str(text or "").split("\n")
    for idx, line in enumerate(lines):
        if _has_stage1_trigger(line):
            window = " ".join(lines[max(0, idx - 1): idx + 3])
            window_pairs, window_totals = _stage1_pairs_from_text(window)
            pairs.extend({**p, "page": page_num} for p in window_pairs)
            totals.extend(window_totals)
        ext_pair, exclusion = _ext_group_pair_from_unit(line)
        if ext_pair is not None:
            pairs.append({**ext_pair, "page": page_num})
        elif exclusion:
            exclusions.append(exclusion)
        pairs.extend(
            {**pair, "page": page_num} for pair in _occupation_share_pairs(line)
        )
    return pairs, totals, exclusions


def _has_stage1_trigger(line: str) -> bool:
    """行是否触发第一阶段窗口捕获（探针口径: 平铺触发词/总分/其中）。"""
    return (
        any(term in line for term in _vp._FLAT_COMPOSITION_TRIGGER_TERMS)
        or "总分" in line
        or "其中" in line
    )


def _declaration_pairs_from_table_grid(
    page_num: int, grid: list[list[str]]
) -> tuple[list[dict], list[float], list[str]]:
    """单表网格捕获: 触发行±1第一阶段对 + 触发行纯数字格总分候选 + 扩展形态整格匹配。"""
    pairs: list[dict] = []
    totals: list[float] = []
    exclusions: list[str] = []
    rows = [[str(cell or "") for cell in row] for row in (grid or [])]
    trigger_rows = {
        i for i, row in enumerate(rows)
        if any(any(term in cell for term in _vp._FLAT_COMPOSITION_TRIGGER_TERMS) for cell in row)
    }
    scan_rows = {i for t in trigger_rows for i in (t - 1, t, t + 1) if 0 <= i < len(rows)}
    for i, row in enumerate(rows):
        row_pairs, row_totals = _stage1_pairs_from_text(" ".join(row))
        if i in scan_rows:
            pairs.extend({**p, "page": page_num} for p in row_pairs)
        else:
            # 非触发窗口行仅保留强特征来源, 防普通规则文本污染（探针口径）。
            pairs.extend(
                {**p, "page": page_num} for p in row_pairs if p["source"] in _NON_SCAN_KEEP_SOURCES
            )
        totals.extend(row_totals)
        if i in trigger_rows:
            totals.extend(float(cell.strip()) for cell in row if _NUMERIC_CELL_RE.match(cell.strip()))
        ext_pairs, ext_exclusions = _ext_pairs_from_row(page_num, row)
        pairs.extend(ext_pairs)
        exclusions.extend(ext_exclusions)
    return pairs, totals, exclusions


def _ext_pairs_from_row(page_num: int, row: list[str]) -> tuple[list[dict], list[str]]:
    """整格扩展形态捕获; 同行权重格(如"10%")对括注式声明附加权重（探针口径）。"""
    row_weight = next(
        (float(cell.strip().rstrip("%％")) / 100.0 for cell in row if _WEIGHT_CELL_RE.match(cell.strip())),
        None,
    )
    pairs: list[dict] = []
    exclusions: list[str] = []
    for cell in row:
        for cell_line in cell.split("\n"):
            ext_pair, exclusion = _ext_group_pair_from_unit(cell_line)
            if exclusion:
                exclusions.append(exclusion)
            if ext_pair is None:
                continue
            if row_weight is not None and ext_pair["source"] == "group_paren":
                ext_pair = {**ext_pair, "weight": row_weight}
            pairs.append({**ext_pair, "page": page_num})
    return pairs, exclusions


def _stage1_pairs_from_text(raw: str) -> tuple[list[dict], list[float]]:
    """volume_parser 第一阶段原文对(平铺/列举/每部分满分/内联)+折算卷定义（探针口径吸收）。"""
    pairs: list[dict] = []
    totals: list[float] = []
    if any(term in raw for term in _vp._FLAT_COMPOSITION_TRIGGER_TERMS):
        pairs.extend(_each_part_pairs(raw))
        pairs.extend(_ledger_flat_pairs(raw))  # 取代 volume_parser 的共享平铺正则
        pairs.extend(_listing_pairs(raw))
    inline_pairs, inline_totals = _inline_pairs(raw)
    pairs.extend(inline_pairs)
    totals.extend(inline_totals)
    pairs.extend(_weighted_def_pairs(raw))
    return pairs, totals


def _each_part_pairs(raw: str) -> list[dict]:
    """"分技术、商务、价格三个部分...每个部分满分均为100分"句式。"""
    match = _vp._EACH_PART_PATTERN.search(raw)
    if not match:
        return []
    labels = (
        _vp.clean_composition_label(t)
        for t in re.split(r"[、，,和及]+", match.group(1))
        if t.strip()
    )
    return [
        {"label": label, "score": float(match.group(2)), "weight": None, "source": "each_part"}
        for label in labels
    ]


def _ledger_flat_pairs(raw: str) -> list[dict]:
    """账本专用平铺配对：保留开放原文连接符标签。

 职责: 用 _LEDGER_FLAT_PAIR_PATTERN 解析平铺构成文本，支持 '-－—_' 连接符
 及字母数字；触发语境约束仍由调用方 _stage1_pairs_from_text 负责。
 不做: 不做标签归一（label_normalization 职责）、不做对账。

 哨兵空格使行首标签也能满足左边界断言。单一账本平铺入口可防止同分值不同标签
 （含/不含前缀）同时进账，避免 reconciliation 出现虚假缺口。
 """
    sentinel_raw = " " + raw
    return [
        {
            "label": _vp.clean_composition_label(m.group(1)),
            "score": float(m.group(2)),
            "weight": None,
            "source": "flat",
        }
        for m in _LEDGER_FLAT_PAIR_PATTERN.finditer(sentinel_raw)
        if not any(t in m.group(1) for t in _vp._FLAT_LABEL_SKIP_TERMS)
        and _vp.clean_composition_label(m.group(1))
        and float(m.group(2)) > 0
    ]


def _listing_pairs(raw: str) -> list[dict]:
    """无冒号构成列举: "分值构成: 商务20分；技术30分；价格50分"。

    门控: 名词触发词后同句扫描 + 标签>=2字 + 至少2对成立才收（防规则文本误捕）。
    """
    trigger = _LISTING_TRIGGER_RE.search(raw)
    if not trigger:
        return []
    tail = raw[trigger.end():]
    end = _LISTING_END_RE.search(tail)
    segment = tail[: end.start()] if end else tail
    found = [
        {
            "label": _vp.clean_composition_label(m.group(1).strip("、，, ")),
            "score": float(m.group(2)),
            "weight": None,
            "source": "flat",
        }
        for m in _LISTING_PAIR_RE.finditer(segment)
        if len(_vp.clean_composition_label(m.group(1).strip("、，, "))) >= 2
        and not any(t in m.group(1) for t in _vp._FLAT_LABEL_SKIP_TERMS)
        and float(m.group(2)) > 0
    ]
    return found if len(found) >= 2 else []


def _occupation_share_pairs(line: str) -> list[dict]:
    """权重构成"X占N分"列举行（, doc2478 实测形态）。

 fail-closed 三闸: 行内含"权重"触发词、≥2 对、合计≈默认百分制 100——
 单对"占N分"(如规则文本"服务占10分")与项内拆分(不含权重语境)均不入账。
 """
    if "权重" not in line:
        return []
    found = [
        {
            "label": m.group(1).strip("、，, "),
            "score": float(m.group(2)),
            "weight": None,
            "source": "occupation_share",
        }
        for m in _OCCUPY_PAIR_RE.finditer(line)
        if len(m.group(1).strip("、，, ")) >= 2
        and not any(t in m.group(1) for t in _vp._FLAT_LABEL_SKIP_TERMS)
        and float(m.group(2)) > 0
    ]
    if len(found) < 2:
        return []
    if abs(sum(p["score"] for p in found) - 100.0) > 1.0:
        return []
    return found


def _inline_pairs(raw: str) -> tuple[list[dict], list[float]]:
    """内联"总分N分,其中..."约束句: 声明总分入候选, 同句段组对入账（探针口径）。"""
    inline = _vp._INLINE_COMPOSITION_TRIGGER_RE.search(raw)
    if not inline:
        return [], []
    tail = raw[inline.end():]
    end = _vp._INLINE_SENTENCE_END_RE.search(tail)
    segment = tail[: end.start()] if end else tail
    pairs: list[dict] = []
    for m in _vp._INLINE_PAIR_RE.finditer(segment):
        label = _vp.clean_composition_label(m.group(1).strip("、，, "))
        if label and not any(t in label for t in _vp._FLAT_LABEL_SKIP_TERMS) and float(m.group(2)) > 0:
            pairs.append({"label": label, "score": float(m.group(2)), "weight": None, "source": "inline"})
    return pairs, [float(inline.group(1))]


def _weighted_share_pairs(raw: str) -> list[dict]:
    """并列卷权重声明对: 卷内原始满分 + 声明的折算权重。

 与 `volume_parser` 共用同一解析器和同一 fail-closed 门禁, 保证账本侧与卷定义侧
 对同一句话得出相同结论——两侧各写一份口径必然漂移(教训)。
 """
    from app.core.extractor.scoring.weighted_share_composition import (
        parse_weighted_share_composition,
    )

    return [
        {
            "label": share.label,
            "score": share.max_score,
            "weight": share.weight,
            "source": "weighted_share",
        }
        for share in parse_weighted_share_composition(raw)
    ]


def _weighted_def_pairs(raw: str) -> list[dict]:
    """折算卷定义对: "技术总分100分...按40%权重折算" → weight=0.4。: 原实现 `search` 取整段首个权重再广播给该段全部卷定义, 同一 chunk
 内多卷各自声明权重时(技术40% / 价格60%)后者被前者覆盖, 折算和随之失真。
 改为按标签出现位置就近配对——每个"X总分N分"只认位于其后、且早于下一个卷定义
 标签的权重声明; 找不到即不产出该对(宁可缺失, 不可错配)。
 """
    label_matches = list(_WEIGHTED_DEF_LABEL_RE.finditer(raw))
    if not label_matches:
        return []
    weight_matches = list(_vp._VOLUME_WEIGHT_PATTERN.finditer(raw))
    if not weight_matches:
        return []
    pairs: list[dict] = []
    for idx, label_match in enumerate(label_matches):
        score = float(label_match.group(2))
        if score <= 0:
            continue
        boundary = (
            label_matches[idx + 1].start() if idx + 1 < len(label_matches) else len(raw)
        )
        weight_match = next(
            (
                w
                for w in weight_matches
                if label_match.end() <= w.start() < boundary
            ),
            None,
        )
        if weight_match is None:
            continue
        pairs.append({
            "label": label_match.group(1),
            "score": score,
            "weight": float(weight_match.group(1)) / 100.0,
            "source": "weighted_def",
        })
    return pairs


def _ext_group_pair_from_unit(unit: str) -> tuple[dict | None, str | None]:
    """扩展形态整行/整格判定; 返回(捕获对, 门控排除原因)。

 组尾缀门控防项级行入组对； 收紧: 以"标准"结尾但非组语义标准形态的
 括注/裸数字分值视为单项自身分值上限, 拒收并计数(item_standard)。
 """
    compact = str(unit or "").strip()
    if not compact or len(compact) > 40:
        return None, None
    exclusion: str | None = None
    for pattern, source in ((_GROUP_PAREN_RE, "group_paren"), (_GROUP_BARE_RE, "group_bare")):
        match = pattern.match(compact)
        if not match or float(match.group(2)) <= 0 or is_total_like_label(match.group(1)):
            continue
        label = match.group(1)
        if not label.endswith(_GROUP_SUFFIX_GATE):
            exclusion = exclusion or "suffix_gate"
            continue
        if label.endswith("标准") and not label.endswith(_STANDARD_GROUP_FORMS):
            exclusion = exclusion or "item_standard"
            continue
        return {"label": label, "score": float(match.group(2)), "weight": None, "source": source}, None
    return None, exclusion


def _dedupe_declaration_pairs(raw_pairs: list[dict]) -> tuple[list[dict], int]:
    """按(归一标签,分值)去重, 同键带权重者优先; 总分/合计类标签跳过并计数。"""
    out: dict[tuple[str, float], dict] = {}
    total_like_skipped = 0
    for pair in raw_pairs:
        if is_total_like_label(pair["label"]):
            total_like_skipped += 1
            continue
        norm = normalize_group_label(pair["label"])
        score = round(float(pair["score"]), 4)
        if not norm or score <= 0:
            continue
        key = (norm, score)
        if key not in out or (pair.get("weight") and not out[key].get("weight")):
            out[key] = {**pair, "norm_label": norm, "score": score}
    return list(out.values()), total_like_skipped


def _split_label_conflicts(deduped: list[dict]) -> tuple[list[dict], list[str]]:
    """ 收紧: 同归一标签不同分值且不可由权重解释 → 整组拒用并 fail-visible。

 注: 探针 v1 对此保留双份; 本模块按 语义改为冲突拒用。
 """
    by_norm: dict[str, list[dict]] = {}
    for pair in deduped:
        by_norm.setdefault(pair["norm_label"], []).append(pair)
    conflicted = {
        norm for norm, group in by_norm.items()
        if len(group) > 1 and not _scores_weight_explained(group)
    }
    kept = [p for p in deduped if p["norm_label"] not in conflicted]
    conflicts = [
        "同标签冲突声明拒用: " + norm + " -> "
        + "; ".join(_pair_signature(p) for p in by_norm[norm])
        for norm in sorted(conflicted)
    ]
    return kept, conflicts


def _scores_weight_explained(group: list[dict]) -> bool:
    """组内任意两条分值是否均可由权重折算互相解释(score_i x weight_i ≈ score_j)。"""
    return all(_weight_links(a, b) for a, b in combinations(group, 2))


def _weight_links(a: dict, b: dict) -> bool:
    """两条声明中一方的折算值与另一方分值相等(容差内)即视为权重可解释。"""
    for x, y in ((a, b), (b, a)):
        weight = x.get("weight")
        if weight and abs(float(x["score"]) * float(weight) - float(y["score"])) <= SCORE_MATCH_TOLERANCE:
            return True
    return False


def _pair_signature(pair: dict) -> str:
    """冲突记录用的单条声明签名: 标签=分值[来源@页码]。"""
    page = pair.get("page")
    return f"{pair['label']}={pair['score']:g}[{pair['source']}@p{page if page is not None else '?'}]"


__all__ = ["capture_scoring_declarations", "declaration_pages_from_signals"]

"""表头驱动的评分表结构化解析（根治）。

职责: 把单个 scoring_table markdown 按"表头列定位 + 按行关联"解析为评分项/卷定义。
 score_weight 只取"满分分值"列, 从根上杜绝"从评分内容文本猜数字"(如把"低于60分"当满分、
 把"X总分100分"当评分项)。
不做: 不调用 LLM/DB; 不做跨表卷权重回填(由 scoring_extractor + volume_parser.apply 完成)。

设计依据(数据库实测的两类真实表):
- 框架表表头: 条款号|条款名称|评审内容|评审因素|权重占比(%)|评分标准
 → 有权重列 + "X总分N分按M%折算" → 解析为卷定义(卷权重/卷满分), 不产评分子项。
- 明细评分表表头: 评分标准|(空子类列)|评分内容|主观/客观/扣分|满分分值
 → 有满分列 → 逐行子项, score_weight 取满分列, description 取评分内容列。
- 响应性/资格审查表: 序号|评审大类|二级评审子类|主要内容
 → 无满分/权重列 → 符合性项(无分值)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.extractor.scoring.category_inference import infer_scoring_category
from app.core.extractor.scoring.column_role_inference import infer_detail_rule_column
from app.core.extractor.scoring.detail_row_semantics import (
    is_volume_group_name,
    leading_rule_label,
    rule_first_segment,
    volume_group_marker,
)
from app.core.extractor.scoring.embedded_score_parser import parse_embedded_score_table
from app.core.extractor.scoring.framework_table_parser import (
    has_volume_definition_row,
    parse_framework_table,
    parse_weight_summary_defs,
)
from app.core.extractor.scoring.group_closure import (
    build_group_closure_diagnostics,
    parse_group_subtotals,
)
from app.core.extractor.scoring.group_column import detect_group_column
from app.core.extractor.scoring.row_group_attribution import (
    backfill_recovered_group_labels,
    recover_row_group_labels,
)
from app.core.extractor.scoring.score_candidates import detect_score_rule_conflict
from app.core.extractor.scoring.score_column_semantics import (
    SCORE_HEADER_TERMS,
    classify_adjustment_fact_type,
    find_embedded_scores,
    is_structural_score_item_name,
    is_total_row,
    parse_adjustment_score,
    parse_score_range_max,
    parse_short_score_tolerant,
)
from app.core.extractor.scoring.scoring_decision_trace import record as _trace
from app.core.extractor.scoring.scoring_grid_payload import (
    markdown_table_to_grid,
    override_column_roles,
)
from app.core.extractor.scoring.scoring_item_naming import normalize_scoring_item_name
from app.core.extractor.scoring.scoring_lot_header import resolve_lot_from_content
from app.core.extractor.scoring.scoring_rule_evidence import (
    backfill_rules_from_payload_roles,
)
from app.core.extractor.scoring.table_scope_resolution import (
    apply_section_title_scope_label,
    scope_labels_for_data_rows,
)
from app.core.extractor.scoring.volume_labeling import (
    infer_group_volume_label,
    infer_initial_table_volume,
    infer_scoring_group_label_from_row,
    infer_volume_label_from_row,
    refine_scoring_group_label,
    service_volume_signal,
    volume_from_score_standard_marker,
)
from app.core.extractor.scoring.volume_parser import (
    VolumeDefinition,
    parse_flat_volume_composition,
    volume_defs_from_group_subtotals,
)
from app.core.extractor.scoring.volume_weight_signals import (
    parse_decimal_volume_weight_defs,
)
from app.core.extractor.scoring.weighted_volume_table import (
    parse_weighted_volume_definitions,
)
from app.core.parser.scoring_table_detector import (
    analyze_scoring_table_rows,
)
from app.schemas.requirement import ExtractedScoringItem

from .numeric_score_column_parser import parse_numeric_score_column_table
from .scoring_parse_selector import select_embedded_or_numeric_parse
from .scoring_row_evidence_recovery import RowRecoveryContext, attempt_row_recovery

# 列角色识别关键词(表头去空格后匹配)
_SCORE_COL_KW = SCORE_HEADER_TERMS
_WEIGHT_COL_KW = ("权重", "占比")
_CONTENT_COL_KW = ("评分内容", "评审因素", "评审内容", "主要内容")
_CATEGORY_COL_KW = ("评分标准", "评审大类", "条款", "评审内容")
# 符合性表表头可辨性(防把乱码/无关表当符合性表)
_COMPLIANCE_HEADER_KW = ("评审", "评分", "应答", "资格", "内容", "标准")
# 评分表特有的"主观/客观/扣分"列信号(C 无表头识别): 资源/进度/培训等非评分表不含,
# 用作表头不可辨(跨页续表表头丢失/乱码)时定位满分列的强信号, 误判风险低。
_SUBJECTIVITY_KW = ("主观", "客观", "扣分")

_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")
_STRICT_SCORE_RE = re.compile(r"^\s*(?:满分)?\s*(\d+(?:\.\d+)?)\s*分?\s*$")
_CLAUSE_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)+(?:[（(]\d+[）)])?$")
# _LOT_HEADER_RE 已移至 ledger/declaration_scope.py（O3 单源），此处由 resolve_chunk_lot 提供
# "总分100分" / "总分 100 分"
_VOLUME_MAX_RE = re.compile(r"总分\s*(\d+(?:\.\d+)?)\s*分")
# 卷定义行特征: 含"总分N分"且含"按M%...折算"
_VOLUME_DEF_RE = re.compile(r"总分\s*\d+(?:\.\d+)?\s*分.*?按\s*\d+(?:\.\d+)?\s*[%％].*?折算")

_ZH_VOLUME_MAP = (
    ("技术", "technical"),
    ("价格", "price"),
    ("商务", "commercial"),
    ("服务", "service"),
    ("资信", "qualification"),
)


@dataclass
class ParsedScoringTable:
    """单表解析结果。confident=False 表示表头不可辨, 交 LLM 兜底。"""

    items: list[ExtractedScoringItem] = field(default_factory=list)
    volume_defs: dict[str, VolumeDefinition] = field(default_factory=dict)
    confident: bool = False
    note: str = ""
    diagnostics: list[str] = field(default_factory=list)


def parse_scoring_table(
    markdown: str,
    *,
    section_path: str = "",
    grid: list[list[str]] | None = None,
    column_roles: dict | None = None,
    row_pages: list[int | None] | None = None,
    scope_rows: list[dict] | None = None,
) -> ParsedScoringTable:
    """按表头列定位解析单个评分表 markdown。

 grid(阶段1.5): 结构化载荷网格, 提供则跳过 markdown 重解析(双轨, 缺省回退)。
 column_roles: 装配器经数据资格赛的列角色, 提供则覆盖表头词面绑定,
 免同一词面歧义("评分内容"实为规则列)在解析层二次错绑。
 row_pages(M1): 与 grid 行序对齐的物理页码, 供确定性解析产物写入 source_pages。
 scope_rows: 装配层解析的表作用域组标签(合计行切分+标题+闭合验证),
 作评分项组标签的最后兜底; 表级标题标签(section_path 后缀)仅在整表零标签、
 零卷定义时介入(table_scope_resolution 收口)。
 """
    result = _parse_scoring_table_routed(
        markdown,
        section_path=section_path,
        grid=grid,
        column_roles=column_roles,
        row_pages=row_pages,
        scope_rows=scope_rows,
    )
    apply_section_title_scope_label(
        result.items, section_path, has_volume_defs=bool(result.volume_defs)
    )
    return result


def _parse_scoring_table_routed(
    markdown: str,
    *,
    section_path: str = "",
    grid: list[list[str]] | None = None,
    column_roles: dict | None = None,
    row_pages: list[int | None] | None = None,
    scope_rows: list[dict] | None = None,
) -> ParsedScoringTable:
    """确定性解析路由主体(公开入口的标题兜底后置在 parse_scoring_table)。"""
    grid = [list(row) for row in grid] if grid else _markdown_to_grid(markdown)
    lot = _extract_lot_from_markdown(markdown)
    if len(grid) < 2:
        _trace("PARSE_ROUTE/no-grid-rows")
        return ParsedScoringTable(confident=False, note="无有效表格行")
    structure = analyze_scoring_table_rows(grid)
    if structure.is_directory_template:
        _trace("PARSE_ROUTE/directory-template-skip")
        return _with_lot(ParsedScoringTable(
            items=[],
            confident=True,
            note="目录模板/投标文件格式表, 跳过",
        ), lot)
    header, data, header_idx = _select_header_and_data(grid)
    data_pages = row_pages[header_idx + 1:] if row_pages and len(row_pages) == len(grid) else None
    scope_labels = scope_labels_for_data_rows(scope_rows, header_idx + 1, len(data))
    roles = _identify_columns(header)
    group_diagnostics = build_group_closure_diagnostics(grid, score_col=roles["score"])
    volume = _infer_table_volume(section_path)
    initial_volume = _infer_volume_heading(grid[0]) or volume
    weight_summary = _parse_weight_summary_table(grid)
    # 平铺"分值构成"声明(如 商务技术20分/价格80分)解析为组基准并随
    # weight_summary.volume_defs 一路 setdefault 合入(已有权威卷定义优先)。
    for vol_key, vdef in _parse_flat_composition_defs(grid).items():
        weight_summary.volume_defs.setdefault(vol_key, vdef)
    for vol_key, vdef in parse_weighted_volume_definitions(header, data).items():
        weight_summary.volume_defs.setdefault(vol_key, vdef)
    for vol_key, vdef in volume_defs_from_group_subtotals(parse_group_subtotals(grid)).items():
        weight_summary.volume_defs.setdefault(vol_key, vdef)
    for vol_key, vdef in parse_decimal_volume_weight_defs(grid).items():
        weight_summary.volume_defs.setdefault(vol_key, vdef)
    if weight_summary.confident and not (structure.item_columns and structure.rule_columns):
        _trace("PARSE_ROUTE/weight-summary-only")
        return _with_lot(weight_summary, lot)

    if roles["score"] is not None and _detail_roles_are_plausible(data, roles):
        _trace("PARSE_ROUTE/detail-lexical-roles")
        lexical_result = _parse_detail_table(
            header, data, roles, initial_volume,
            row_pages=data_pages, scope_labels=scope_labels,
        )
        lexical_result.items = backfill_rules_from_payload_roles(
            lexical_result.items, data, roles, column_roles
        )
        return _with_lot(_with_diagnostics(_with_volume_defs(
            lexical_result,
            weight_summary.volume_defs,
        ), group_diagnostics), lot)
    if roles["weight"] is not None and _has_volume_def(data, roles["weight"]):
        _trace("PARSE_ROUTE/framework-table")
        return _with_lot(_parse_framework_table(header, data, roles), lot)
    # C: 表头不可辨但数据行有"主观客观列+相邻满分数字列"特征 → 按明细表确定性解析,
    # 救回跨页续表(如 page34 技术方案表)因表头丢失被 LLM 兜底漏行的满分(整体建设/实施计划)。
    headerless_roles = _infer_headerless_detail_roles(data)
    if headerless_roles is not None:
        result = _parse_detail_table(
            header, data, headerless_roles, initial_volume,
            row_pages=data_pages, scope_labels=scope_labels,
        )
        if result.items:
            _trace("PARSE_ROUTE/detail-headerless-roles")
            return _with_lot(
                _with_diagnostics(_with_volume_defs(result, weight_summary.volume_defs), group_diagnostics),
                lot,
            )
    # 有独立分值列但表头错位/前导小表干扰导致列角色识别失败时,
    # 按列统计(而非表头关键词)逐行识别"评分对象+具体分值+评分标准"三元评分项。
    #
    # 三列表可能同时触发价格公式嵌入式解析与独立分值列解析。不能让
    # embedded 的局部成功(价格20)短路整表, 必须让两个确定性候选仲裁。
    embedded_items = parse_embedded_score_table(
        header, data, section_path=section_path, scope_labels=scope_labels
    )
    numeric_col_result = parse_numeric_score_column_table(grid, initial_volume)
    selected = select_embedded_or_numeric_parse(
        embedded_items=embedded_items, numeric_result=numeric_col_result
    )
    payload_roles = override_column_roles(roles, column_roles)
    payload_result = None
    if (
        payload_roles is not roles
        and payload_roles["score"] is not None
        and _detail_roles_are_plausible(data, payload_roles)
    ):
        payload_result = _parse_detail_table(
            header, data, payload_roles, initial_volume, scope_labels=scope_labels
        )
    if (
        selected is not None
        and payload_result is not None
        and len(payload_result.items) > len(selected.items)
    ):
        _trace("PARSE_ROUTE/payload-roles-outbid-selected")
        return _with_lot(
            _with_diagnostics(
                _with_volume_defs(payload_result, weight_summary.volume_defs),
                group_diagnostics,
            ),
            lot,
        )
    if selected is not None:
        _trace("PARSE_ROUTE/embedded-or-numeric-column")
        selected_items = backfill_rules_from_payload_roles(
            list(selected.items), data, roles, column_roles
        )
        if data_pages:
            available_pages = [page for page in data_pages if isinstance(page, int)]
            for item, page in zip(selected_items, available_pages):
                if not getattr(item, "source_pages", None):
                    item.source_pages = [page]
        selected_result = ParsedScoringTable(
            items=selected_items,
            confident=True,
            note=selected.note,
            diagnostics=list(selected.diagnostics or []),
        )
        return _with_lot(
            _with_diagnostics(
                _with_volume_defs(selected_result, weight_summary.volume_defs),
                group_diagnostics,
            ),
            lot,
        )
    # 全部既有确定性路径失败时, 用装配器经数据资格赛的列角色最后重试
    # 明细路径(词面歧义如"评分内容"实为规则列时的兜底), 仍失败才落符合性判定。
    # 置于末位: 既有路径已正确的表(如序号列被词面误当name)不被载荷角色抢跑。
    if payload_result is not None:
        result = payload_result
        if result.items:
            _trace("PARSE_ROUTE/payload-roles-last-retry")
            return _with_lot(
                _with_diagnostics(
                    _with_volume_defs(result, weight_summary.volume_defs),
                    group_diagnostics,
                ),
                lot,
            )
    # 结构分析已检出真实分值列的表禁止确定性跳过——confident=True + items=[]
    # 会关闭整条 L1/L2/L3 救援链且无前端提示(1936 静默零提取)。此时必须交 LLM 兜底。
    if structure.score_columns:
        _trace("PARSE_ROUTE/score-columns-defer-to-llm")
        return _with_lot(
            ParsedScoringTable(
                confident=False,
                note="含分值列但确定性解析未命中, 交LLM兜底",
                diagnostics=["评分表含分值列但全部确定性路径失配, 已转LLM提取"],
            ),
            lot,
        )
    _trace("PARSE_ROUTE/compliance-table")
    return _with_lot(_parse_compliance_table(header, data, roles, volume), lot)


def _with_volume_defs(
    result: ParsedScoringTable,
    volume_defs: dict[str, VolumeDefinition],
) -> ParsedScoringTable:
    """把同表内解析出的卷权重定义并入评分明细结果。"""
    if not volume_defs:
        return result
    merged = dict(result.volume_defs)
    for volume, definition in volume_defs.items():
        merged.setdefault(volume, definition)
    result.volume_defs = merged
    return result


def _with_lot(result: ParsedScoringTable, lot: str | None) -> ParsedScoringTable:
    """把页头中的采购包/标段标签回填到解析出的评分项。"""
    result = _drop_non_scoring_fact_rows(result)
    if not lot:
        return result
    for item in result.items:
        try:
            item.lot = lot
        except (AttributeError, ValueError):
            pass
    return result


# 混合评分区段中的"结论/程序"行不是评分项, 必须行级剔除而非整表降级——
# 整表降级会把全部真实评分行推给更弱的 L1 单 chunk LLM 兜底(1934 实测 16/100 更差)。
_NON_SCORING_FACT_NAME_MARKERS: tuple[str, ...] = (
    "综合得分",
    "评分计算原则",
    "推荐原则",
)


def _drop_non_scoring_fact_rows(result: ParsedScoringTable) -> ParsedScoringTable:
    """按行剔除综合得分公式/候选人推荐原则等非评分行, 保留真实评分行。"""
    if not result.items:
        return result
    kept: list[ExtractedScoringItem] = []
    dropped: list[str] = []
    for item in result.items:
        name = str(getattr(item, "score_item_name", "") or "").replace(" ", "")
        if any(marker in name for marker in _NON_SCORING_FACT_NAME_MARKERS):
            score = float(getattr(item, "score_weight", 0) or 0)
            dropped.append(f"{name}({score:g}分)")
            continue
        kept.append(item)
    if dropped:
        result.items = kept
        result.diagnostics = list(dict.fromkeys([
            *result.diagnostics,
            f"已剔除非评分项行(结论/程序说明): {'、'.join(dropped)}",
        ]))
    return result


def _with_diagnostics(
    result: ParsedScoringTable,
    diagnostics: list[str],
) -> ParsedScoringTable:
    """把上层诊断并入解析结果, 保持顺序并去重。"""
    if not diagnostics:
        return result
    merged = list(dict.fromkeys([*result.diagnostics, *diagnostics]))
    result.diagnostics = merged
    return result


def _extract_lot_from_markdown(markdown: str) -> str | None:
    """O3: 薄委托 scoring_lot_header.resolve_lot_from_content（分层单源，消灭重复首行逻辑）。

    Parser 不 import ledger 包——两者共同依赖中性模块 scoring_lot_header。
    """
    return resolve_lot_from_content(markdown or "")


# ---------------------------------------------------------------------------
# markdown 解析
# ---------------------------------------------------------------------------


# markdown->网格单源实现收口到 scoring_grid_payload(阶段1.5), 本地别名保持既有调用点
_markdown_to_grid = markdown_table_to_grid


def _identify_columns(header: list[str]) -> dict[str, int | None]:
    """按表头关键词识别列角色。"""
    roles: dict[str, int | None] = {
        "score": None,
        "weight": None,
        "content": None,
        "category": None,
        "name": None,
        "rule": None,
    }
    for i, cell in enumerate(header):
        h = (cell or "").replace(" ", "")
        if roles["weight"] is None and any(k in h for k in _WEIGHT_COL_KW):
            roles["weight"] = i
        # "评分因素权重分值"是父级满分/权重列, 不能当子项"分值"列。
        if (
            roles["score"] is None
            and any(k in h for k in _SCORE_COL_KW)
            and not any(k in h for k in _WEIGHT_COL_KW)
        ):
            roles["score"] = i
        if roles["content"] is None and any(
            k in h
            for k in (
                "细分项",
                "评分内容",
                "评审内容",
                "主要内容",
                "评分项目",
                "评分项",
                "评审项目",
                "评分对象",
                "计分因素",
            )
        ):
            roles["content"] = i
        if roles["name"] is None and any(
            k in h
            for k in (
                "评分因素",
                "评审因素",
                "二级评审子类",
                "评分项目",
                "评分项",
                "评审项目",
                "评分对象",
                "计分因素",
            )
        ):
            roles["name"] = i
        if roles["category"] is None and any(k in h for k in ("条款", "评审大类")):
            roles["category"] = i
        if roles["rule"] is None and (
            any(k in h for k in ("评分标准", "评审标准", "评议标准", "评定标准", "评定内容及标准", "主要内容"))
            or ("内容" in h and "标准" in h)
        ):
            roles["rule"] = i
            if roles["category"] is None:
                roles["category"] = i
    if roles["content"] is None and roles["name"] is not None:
        roles["content"] = roles["name"]
    return roles


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _parse_number(text: str) -> float | None:
    m = _NUMBER_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _parse_score_value(text: str) -> float | None:
    """严格解析分值单元格(短分值/水印噪声容忍/区间上限); 不从评分规则正文猜数字。

 区间型 `0-30`: 分档评分表满分列常写为可得分区间, 上限即该项满分。
 水印噪声型 `20分 照`: 页面水印字穿透进分值格, 按孤立噪声 token 剥离。
 仅在此单点接入, parse_short_score 本体语义不动, 其他消费者不受影响。
 """
    value = parse_short_score_tolerant(text)
    if value is not None:
        return value
    return parse_score_range_max(text)


def _is_clause_number(text: str) -> bool:
    normalized = (text or "").replace(" ", "")
    return bool(_CLAUSE_NUMBER_RE.match(normalized))


def _majority(count: int, total: int) -> bool:
    """命中数是否达到"多数行"门槛(至少2行且过半)。"""
    return count >= max(2, (total + 1) // 2)


def _select_header_and_data(grid: list[list[str]]) -> tuple[list[str], list[list[str]], int]:
    """选择真实评分表头并剔除分组表头行；不解析评分项内容。"""
    limit = min(len(grid) - 1, 3)
    for idx in range(limit):
        header = _merge_group_headers(grid[:idx], grid[idx])
        data = grid[idx + 1:]
        roles = _identify_columns(header)
        if roles["score"] is not None and _detail_roles_are_plausible(data, roles):
            return header, data, idx
        if roles["weight"] is not None and _has_volume_def(data, roles["weight"]):
            return header, data, idx
    return grid[0], grid[1:], 0


def _merge_group_headers(prefix_rows: list[list[str]], header: list[str]) -> list[str]:
    """把上层分组表头补到真实列头的空白列, 保留真实列头优先级。"""
    merged = list(header)
    for row in prefix_rows:
        for idx, cell in enumerate(row):
            if idx >= len(merged):
                continue
            if not (merged[idx] or "").strip() and (cell or "").strip():
                merged[idx] = str(cell)
    return merged


def _detail_roles_are_plausible(
    data: list[list[str]], roles: dict[str, int | None]
) -> bool:
    """校验明细表列角色是否可信; 防止把条款号列误判为分值列。

 合并单元格明细表: 同一评审因素的多个分档子行共享合并单元格,
 内容列在续接子行为空(名称在规则列前缀), content_hits 天然少于 score_hits;
 此时只要每个计分行在内容列或规则列有中文描述, 仍判可信走确定性解析。

 注意: 本层不做 name/rule 取向资格赛——parser 的 content 列语义
 允许承载长文本(与装配器 name=短对象名语义不同), 取向纠偏收口在装配器层
 (canonical 渲染前)与载荷角色透传, 在此加闸会误伤既有正确词面路径。
 """
    score_i = roles.get("score")
    content_i = roles.get("content")
    name_i = roles.get("name")
    rule_i = roles.get("rule")
    if score_i is None or content_i is None:
        return False
    score_hits = 0
    clause_hits = 0
    content_hits = 0
    item_name_hits = 0
    described_score_hits = 0
    for row_idx, row in enumerate(data):
        if is_total_row(row):
            continue
        score_text = _cell(row, score_i)
        strict_score = _parse_score_value(score_text)
        content_text = _cell(row, content_i)
        name_text = _cell(row, name_i) if name_i is not None else content_text
        has_content_zh = bool(re.search(r"[\u4e00-\u9fff]", content_text))
        if strict_score is not None:
            score_hits += 1
            if (
                re.search(r"[\u4e00-\u9fff]", name_text)
                and not is_structural_score_item_name(name_text)
            ):
                item_name_hits += 1
            if has_content_zh or re.search(r"[\u4e00-\u9fff]", _cell(row, rule_i)):
                described_score_hits += 1
        elif _is_clause_number(score_text):
            clause_hits += 1
        if has_content_zh:
            content_hits += 1
    if score_hits < 1 or score_hits <= clause_hits:
        return False
    if item_name_hits < 1:
        return False
    if content_hits >= score_hits:
        return True
    return content_hits >= 1 and described_score_hits == score_hits


def _infer_headerless_detail_roles(data: list[list[str]]) -> dict[str, int | None] | None:
    """表头不可辨(跨页续表表头丢失/被乱码占位)时, 用数据行特征定位明细表列角色(C)。

 判定(强信号 AND, 防误判): 多数数据行存在"主观/客观/扣分"单元格(评分表特有), 且其右邻列
 多数为有效满分数字(0<分值<=100)。命中则返回 {score, content, category} 角色, 否则 None
 (交既有 compliance/LLM 兜底)。资源/进度/培训等非评分表不含主观客观列, 故不被误判。

 定位依据(数据库实测 page34 续表): 列序为 大类|因素|评分内容|主观客观|满分分值,
 满分=主观客观列右邻, 评分内容=主观客观列左邻, 大类=首列。
 """
    if len(data) < 2:
        return None
    width = max((len(r) for r in data), default=0)
    if width < 3:
        return None
    subj_col: int | None = None
    for ci in range(width):
        hits = sum(1 for r in data if any(k in _cell(r, ci) for k in _SUBJECTIVITY_KW))
        if _majority(hits, len(data)):
            subj_col = ci
            break
    if subj_col is None or subj_col + 1 >= width:
        return None
    score_i = subj_col + 1
    numeric_hits = 0
    for r in data:
        val = _parse_number(_cell(r, score_i))
        if val is not None and 0 < val <= 100:
            numeric_hits += 1
    if not _majority(numeric_hits, len(data)):
        return None
    content_i = subj_col - 1 if subj_col - 1 >= 0 else None
    return {"score": score_i, "weight": None, "content": content_i, "category": 0}


# ---------------------------------------------------------------------------
# 表类型解析
# ---------------------------------------------------------------------------


def _parse_detail_table(
    header: list[str],
    data: list[list[str]],
    roles: dict[str, int | None],
    volume: str,
    *,
    row_pages: list[int | None] | None = None,
    scope_labels: dict[int, str] | None = None,
) -> ParsedScoringTable:
    """明细评分表: score_weight 取满分列, description 取内容列, 续行并入上一项。

 scope_labels: 装配层表作用域标签(数据行序), 作组标签最后兜底。
 """
    score_i = roles["score"]
    weight_i = roles.get("weight")
    content_i = roles["content"]
    if content_i is None:
        content_i = _guess_content_col(score_i, len(header))
    cat_i = roles["category"] if roles["category"] is not None else 0
    name_i = roles.get("name")
    if name_i is None:
        name_i = content_i - 1 if content_i is not None and (content_i - 1) > cat_i else None
    rule_i = roles.get("rule")
    if rule_i is None and score_i is not None:
        # 规则列可在分值列任一侧("评审项目|分值|评审内容" 与
        # "评分项目|评分内容|分值"), 且可能因 content 角色被同义词列抢占而无主;
        # 收口到方向无关、覆盖全部未分配列的推断, 杜绝按排列方向逐例打补丁。
        rule_i = infer_detail_rule_column(
            header,
            data,
            score_col=score_i,
            exclude={score_i, weight_i, content_i, cat_i, name_i},
        )
    content_header = _cell(header, content_i).replace(" ", "")
    content_is_item_name = "细分项" in content_header

    items: list[ExtractedScoringItem] = []
    diagnostics: list[str] = []
    last_item: ExtractedScoringItem | None = None
    last_cat = ""
    last_rule = ""
    current_volume = volume
    current_volume_label: str | None = None
    # 分组列(结构+父子闭合)命中时逐行组归属为原文标签权威来源, 优先于
    # 词面推断——`_volume_label_has_signal` 的关键词表对"设计方案/施工方案"这类
    # 不含表内词面的真实大类必然漏失, 致账本无组标签可对账。未命中返回空映射,
    # 完全走既有词面逻辑, 对不满足结构判据的表零行为变化。
    structural_detection = detect_group_column(data)
    structural_row_labels = (
        structural_detection.row_labels if structural_detection is not None else {}
    )
    # `detect_group_column` 只认"名称（N分）"形态的分组列; 组名写在合并
    # 单元格或长文本单元格里(2475"承包人建议书和承包人实施方案"、2476"技术标(汇总
    # 规则:…)")时全表零标签, 账本侧即"100分未归入任何评分卷"。此处按合并单元格继承
    # + 项名前缀家族补一层结构判据。
    # 注意其证据强度低于 detect_group_column(后者经父子分值闭合验证), 故只作为
    # `current_volume_label` 的兜底来源, 仍需过 refine——否则 1707 的原文粗标签
    # "其他因素"会顶替掉按项名细分出的"业绩/信誉"(已建立的语义)。
    recovered_row_labels = (
        {}
        if structural_row_labels
        else recover_row_group_labels(data, name_col=name_i, score_col=score_i)
    )
    # 分值嵌在名称格的明细表(28"项目负责人（3分）")没有可扫描的分值列,
    # 组归属须在循环后按已建项(行号,分值,项名)回填, 与嵌入式路径同一实现。
    built_rows: list[tuple[int, ExtractedScoringItem]] = []
    for row_idx, row in enumerate(data):
        structural_label = structural_row_labels.get(row_idx)
        recovered_label = recovered_row_labels.get(row_idx)
        scope_label = (scope_labels or {}).get(row_idx)
        scoring_group_label = infer_scoring_group_label_from_row(row)
        volume_hint = _infer_volume_heading(row)
        if volume_hint is not None:
            current_volume = volume_hint
            current_volume_label = infer_volume_label_from_row(row)
            # 纯卷分组标题行(仅一格非空且为"XX部分"显式短标记, 如"报价部分（30分）")
            # 只切换卷别, 不得落入续行分支把组名并进上一评分项 description。
            # 显式标记判定与 _infer_volume_heading 的 explicit 分支同源, 避免把恰含
            # "技术"等字样的单格规则续写文本(真续行)误跳过。
            explicit_marker = _volume_from_short_marker_cells(
                [_cell(row, i).replace(" ", "") for i in range(min(len(row), 4))]
            )
            if (
                explicit_marker is not None
                and sum(1 for cell in row if (cell or "").strip()) <= 1
            ):
                _trace("VOLUME/title-row-skip")
                continue
        elif scoring_group_label is not None:
            _trace("VOLUME/group-label-row-skip")
            current_volume_label = scoring_group_label
            continue
        # 多卷评分表的卷分组合并单元格(如"资信标（100分）")与首个子项同行,
        # 只切换卷别、不产评分项; 该行其余列仍按明细子项解析。
        group_volume = volume_group_marker(row, content_i)
        if group_volume is not None:
            _trace("VOLUME/merged-cell-group-marker")
            current_volume = group_volume
            current_volume_label = infer_group_volume_label(row, content_i)
        if is_total_row(row):
            _trace("ROW/total-row-skip")
            continue
        cat_val = _cell(row, cat_i)
        if cat_val:
            last_cat = cat_val
        content = _cell(row, content_i)
        compact_row = "".join(_cell(row, i).replace(" ", "") for i in range(len(row)))
        if "分值构成" in compact_row and "评分标准" in compact_row:
            continue
        score_cell = _cell(row, score_i)
        score = _parse_score_value(score_cell)
        if score is None and weight_i is not None and content:
            # 复杂评分表中, 单项(如评标价/信誉)可能把满分放在父级"权重分值"列,
            # 而"分值"列被 pdfplumber 塞入评分规则正文。此时只取严格分值格式的父级列。
            score = _parse_score_value(_cell(row, weight_i))
        if score is None:
            # 分值列为空但名称自带 `（N分）` 的子项(sample28 `项目负责人（3分）`),
            # 分值是名称内的原文证据; 只取名称格自身的嵌入分值, 不扫规则正文(守 红线)。
            embedded = find_embedded_scores(_cell(row, name_i)) if name_i is not None else []
            if len(embedded) == 1:
                score = embedded[0][1]
        if score is None:
            row_name = _cell(row, name_i).replace(" ", "") if name_i is not None else ""
            fallback_name = row_name or content.replace(" ", "") or _cell(row, cat_i).replace(" ", "")
            # 阶段3(样本18"投"截断): 竖排断字名过短时用规则列前缀标签补全诊断可读性
            if len(fallback_name) <= 2:
                rule_cell = _cell(row, rule_i) if rule_i is not None else ""
                readable = leading_rule_label(rule_cell) or rule_first_segment(rule_cell)
                if len(readable) > len(fallback_name):
                    fallback_name = readable
            # 分值恒0的说明性评审项与 -N 扣分项是评审事实, 不得静默丢弃。
            # 建模为调整项(penalty/base_score), 不进入基础满分构成, 故不影响卷合计。
            adj_rule = _cell(row, rule_i) if rule_i is not None else ""
            adjustment = parse_adjustment_score(score_cell)
            if adjustment is not None and fallback_name:
                adj_item = _build_adjustment_item(
                    name=fallback_name,
                    score=adjustment,
                    rule=adj_rule,
                    content=content,
                    category_hint=last_cat,
                    volume=current_volume,
                    volume_label=(
                        structural_label
                        or current_volume_label
                        or recovered_label
                        or scope_label
                    ),
                )
                if adj_item is not None:
                    if row_pages and row_idx < len(row_pages) and isinstance(row_pages[row_idx], int):
                        adj_item.source_pages = [row_pages[row_idx]]
                    items.append(adj_item)
                    last_item = adj_item
                    continue
            # OCR噪声分值格 → 委托 attempt_row_recovery 做证据推断与项目物化
            _row_page = (
                row_pages[row_idx]
                if row_pages and row_idx < len(row_pages) and isinstance(row_pages[row_idx], int)
                else None
            )
            rec_item = attempt_row_recovery(RowRecoveryContext(
                fallback_name=fallback_name, score_cell=score_cell,
                rule_text=adj_rule, content_text=content,
                last_cat=last_cat, current_volume=current_volume,
                volume_label=(structural_label or current_volume_label or recovered_label or scope_label),
                row_page=_row_page,
            ))
            _trace("ROW/score-cell-recovery-attempt")
            if rec_item is not None:
                items.append(rec_item)
                last_item = rec_item
                continue
            if fallback_name and _looks_like_failed_score_cell(score_cell):
                diagnostics.append(_format_failed_score_row(fallback_name, score_cell))
            # 续行(满分列空): 并入上一项 description + score_rules（同步合并 rule 列）
            if last_item is not None:
                rule_continuation = _cell(row, rule_i) if rule_i is not None else ""
                _merge_continuation_into_item(last_item, content, rule_continuation)
            continue

        rule_parts: list[str] = []
        if rule_i is not None:
            rule_text = _cell(row, rule_i)
            if rule_text:
                rule_parts.append(rule_text)
        if score_cell and _parse_score_value(score_cell) is None and not _is_clause_number(score_cell):
            rule_parts.append(score_cell)
        rule = " ".join(dict.fromkeys(rule_parts)).strip()
        if rule:
            last_rule = rule
        elif last_rule and not cat_val:
            # 仅在同一合并单元格组内（cat_val 空=仍在上一分组）继承规则，
            # 避免把上一评分项规则错挂到有自己 cat_val 的独立新项。
            rule = last_rule

        raw_name = _cell(row, name_i).replace(" ", "") if name_i is not None else ""
        if (
            raw_name
            and content
            and is_volume_group_name(raw_name)
        ):
            name = content.replace(" ", "")
        elif content_is_item_name and content and (len(content) <= 40 or not raw_name):
            name = content.replace(" ", "")
        else:
            name = raw_name
        if not name:
            # category 列实为规则列(无独立大类列的表)时, last_cat 是长规则正文,
            # 不能作评分项名; 改从规则列前缀"XXX："提取子项名。
            cat_for_name = "" if cat_i == rule_i else last_cat.replace(" ", "")
            if current_volume == "price":
                price_text = f"{content}{rule}".replace(" ", "")
                if "报价评分" in price_text:
                    name = "报价评分"
                elif current_volume_label:
                    name = current_volume_label.replace("部分", "").replace("评审", "")
                else:
                    name = "报价评分"
            else:
                name = (
                    content[:24]
                    or cat_for_name
                    or leading_rule_label(rule)
                    or rule_first_segment(rule)
                    or "评分项"
                )
        if current_volume == "price" and (len(name) > 30 or "报价评分" in f"{content}{rule}"):
            price_text = f"{content}{rule}{name}"
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
        if (
            current_volume == "price"
            and name == "价格"
            and current_volume_label == "价格部分"
            and score in {10.0, 30.0, 100.0}
        ):
            name = "报价评分"
        if current_volume == "price" and name == "价格" and "有效响应报价" in f"{content}{rule}":
            name = "有效响应报价"
        name = normalize_scoring_item_name(name, rule)
        desc = " ".join(dict.fromkeys(part for part in (content or name, rule) if part)).strip()
        category, category_basis = infer_scoring_category(f"{last_cat}{name}{desc}")
        # 空分组时「X标评分标准」是卷分段标记, 粘住 current_volume 直到下一标记。
        # 本行有结构分组列或卷分组合并单元格时分组优先, 不让项内短语抢卷。
        if structural_label is None and group_volume is None:
            marker = volume_from_score_standard_marker(f"{name} {content} {rule}")
            if marker is not None:
                current_volume = marker[0]
                current_volume_label = marker[1]
        item = _build_item(
            category=category,
            category_basis=category_basis,
            name=name,
            description=desc,
            score_weight=score,
            volume=current_volume,
            # 结构标签优先且不经细分改写; 词面路径无标签时留 None
            # 交给卷定义回填, 禁止按项名关键词造标签(refine 内含 None 守卫)。
            # 装配层作用域标签(结构+闭合验证)是词面全空时的最后兜底。
            volume_label=(
                structural_label
                or refine_scoring_group_label(
                    current_volume_label or recovered_label, name
                )
                or scope_label
            ),
            score_rules=[rule] if rule else [],
        )
        if item is not None:
            if row_pages and row_idx < len(row_pages) and isinstance(row_pages[row_idx], int):
                item.source_pages = [row_pages[row_idx]]
            # 阶段3: 分值列与规则文本显式满分声明矛盾时产出 score_conflict 诊断(不改值)
            conflict = detect_score_rule_conflict(score, rule)
            if conflict:
                diagnostics.append(f"{name}: {conflict}")
            items.append(item)
            built_rows.append((row_idx, item))
            last_item = item
    if not structural_row_labels and not recovered_row_labels:
        # 组归属证据在"锚点格自带声明值"形态下只有解析完分值才可验证,
        # 循环后按(行号,分值,项名)回填, 判据收口 row_group_attribution。
        backfill_recovered_group_labels(
            data,
            [(idx, it) for idx, it in built_rows if float(it.score_weight or 0) > 0],
            leading_rows=[header],
        )
    return ParsedScoringTable(
        items=items,
        confident=len(items) > 0,
        note=f"明细表 {len(items)} 项",
        diagnostics=diagnostics,
    )


def _parse_framework_table(
    header: list[str], data: list[list[str]], roles: dict[str, int | None]
) -> ParsedScoringTable:
    """框架表 → 卷定义(+可选price合成事实); 解析收口 framework_table_parser。"""
    volume_defs, items = parse_framework_table(header, data, roles)
    return ParsedScoringTable(
        items=items,
        volume_defs=volume_defs,
        confident=len(volume_defs) > 0,
        note=f"框架表 {len(volume_defs)} 卷",
    )


def _parse_flat_composition_defs(grid: list[list[str]]) -> dict[str, VolumeDefinition]:
    """扫描网格中的平铺"分值构成"行, 解析为组基准卷定义。"""
    for row in grid:
        defs = parse_flat_volume_composition(" ".join(cell for cell in row if cell))
        if defs:
            return defs
    return {}


def _parse_weight_summary_table(grid: list[list[str]]) -> ParsedScoringTable:
    """评分汇总表 → 卷权重定义(解析收口 framework_table_parser)。"""
    volume_defs = parse_weight_summary_defs(grid)
    return ParsedScoringTable(
        volume_defs=volume_defs,
        confident=len(volume_defs) >= 2,
        note=f"评分汇总权重表 {len(volume_defs)} 卷" if volume_defs else "",
    )


def _parse_compliance_table(
    header: list[str], data: list[list[str]], roles: dict[str, int | None], volume: str
) -> ParsedScoringTable:
    """无满分列、无权重列的表 → 非评分明细表, 不产评分项(追修)。

 这类表是"响应性评审/资格审查"(否决性符合条款)或"技术规范/进度/资源测算"等说明性表格,
 本质不是评分项(无分值)。此前逐行提取为无分值 scoring_item, 在确认页堆成大量"评分项·重点"
 误导用户以为评分项丢了分值, 且与 tender_extractor 的 normal(否决项)通道重复。
 改为: 识别为非评分明细表即跳过(不产 scoring_item、也不触发 LLM 兜底重提);
 这类否决/符合条款仍由 tender_extractor 走 normal 通道审查。

 data/roles/volume 保留在签名中与其他表解析器一致, 本路径不使用。
 """
    del data, roles, volume  # 显式声明本路径不消费(避免 lint 未用告警)
    header_text = "".join(header).replace(" ", "")
    if not any(k in header_text for k in _COMPLIANCE_HEADER_KW):
        return ParsedScoringTable(confident=False, note="表头不可辨, 交LLM兜底")
    return ParsedScoringTable(items=[], confident=True, note="非评分明细表(符合性/说明), 跳过")


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def _merge_continuation_into_item(
    item: ExtractedScoringItem,
    content: str,
    rule_continuation: str,
) -> None:
    """就地将续行的 content 和 rule 列文本合并入已有评分项。

    职责: 就地更新 description（追加 content/rule_continuation）和 score_rules（去重追加 rule_continuation）。
    不做: 不创建新项；不读取行数据；不判断是否续行。
    """
    if content:
        item.description = (item.description + " " + content).strip()
    if rule_continuation and rule_continuation not in item.score_rules:
        item.description = (item.description + " " + rule_continuation).strip()
        item.score_rules.append(rule_continuation)


def _looks_like_failed_score_cell(text: str) -> bool:
    """分值格含数字但未能解析时, 作为 fail-visible 诊断候选。"""
    raw = text or ""
    if "%" in raw or "％" in raw:
        return False
    return bool(re.search(r"\d", raw))


def _format_failed_score_row(name: str, score_cell: str) -> str:
    return f"疑似评分行分值未识别: {name} | 分值格={score_cell}"


def _guess_content_col(score_i: int | None, width: int) -> int:
    """无显式内容列时, 取满分列左侧最近的非主观列(经验: score-2)。"""
    if score_i is None:
        return max(0, width - 1)
    return max(0, score_i - 2)


def _find_col(header: list[str], keywords: tuple[str, ...]) -> int | None:
    for i, cell in enumerate(header):
        h = (cell or "").replace(" ", "")
        if any(k in h for k in keywords):
            return i
    return None


def _has_volume_def(data: list[list[str]], weight_i: int) -> bool:
    """框架表判定(判据收口 framework_table_parser); weight_i 保留签名兼容。"""
    del weight_i
    return has_volume_definition_row(data)


def _infer_table_volume(section_path: str) -> str:
    """明细评分表所属加权卷(: 收口到 volume_labeling 单源映射)。"""
    return infer_initial_table_volume(section_path)


def _infer_volume_heading(row: list[str]) -> str | None:
    """仅从短分卷标题行继承卷别, 不从长评分规则正文误判。"""
    compact_cells = [_cell(row, i).replace(" ", "") for i in range(min(len(row), 4))]
    non_empty = [cell for cell in compact_cells if cell]
    if not non_empty:
        return None
    joined = "".join(non_empty)
    explicit = _volume_from_short_marker_cells(compact_cells)
    if explicit is not None:
        return explicit
    has_score = any(_parse_score_value(_cell(row, i)) is not None for i in range(len(row)))
    if has_score:
        return None
    if len(joined) > 40:
        return None
    if "报价部分" in joined or "价格部分" in joined:
        return "price"
    if "商务部分" in joined or "综合部分" in joined:
        return "commercial"
    if "技术部分" in joined:
        return "technical"
    if "服务部分" in joined:
        return "service"
    if "资信部分" in joined:
        return "qualification"
    # 装配器 canonical 分组标题行(整行仅首格有值, 如"商务技术评审"/"价格评审"),
    # 按与组基准同源的 price>commercial>technical 顺序映射, 保证组/项卷别一致。
    # 判据是"首格独占", 位置必须校验。单格落在右侧列(如稀疏投影后
    # 规则列独占的换行续写文本)属规则正文, 当作卷标题会整段改写后续项的卷别。
    filled = [(i, _cell(row, i).replace(" ", "")) for i in range(len(row)) if _cell(row, i)]
    if len(filled) == 1 and filled[0][0] == 0 and len(filled[0][1]) <= 20:
        marker = filled[0][1]
        if any(k in marker for k in ("报价", "价格", "评标价")):
            return "price"
        if "商务" in marker:
            return "commercial"
        if "技术" in marker:
            return "technical"
        if service_volume_signal(marker):
            return "service"
    return None


def _volume_from_short_marker_cells(cells: list[str]) -> str | None:
    """从短分组单元格识别卷别；长评分规则正文不参与，防止把`价格分`误当卷标题。"""
    for cell in cells:
        if not cell or len(cell) > 30:
            continue
        if "报价部分" in cell or "价格部分" in cell:
            return "price"
        if "商务部分" in cell or "综合部分" in cell:
            return "commercial"
        if "技术部分" in cell:
            return "technical"
        if "服务部分" in cell:
            return "service"
        if "资信部分" in cell:
            return "qualification"
    return None


def _build_adjustment_item(
    *,
    name: str,
    score: float,
    rule: str,
    content: str,
    category_hint: str,
    volume: str,
    volume_label: str | None,
) -> ExtractedScoringItem | None:
    """构造零分/负分调整项。

 职责: 把"分值恒0的说明性评审项"与"-N 扣分项"建模为调整事实, 保留原文名与规则。
 不做: 不改基础满分构成、不推断卷基准、不参与档次带归并。

 负分表达约定(与 embedded_score_parser 一致, 因 schema 限定 score_weight>=0):
 score_weight 归零、扣分幅度记入 deduction_rules, 避免负分穿透进满分合计。
 """
    clean = normalize_scoring_item_name(name, rule)
    if not clean or is_structural_score_item_name(clean):
        return None
    desc = " ".join(dict.fromkeys(part for part in (content or clean, rule) if part)).strip()
    category, category_basis = infer_scoring_category(f"{category_hint}{clean}{desc}")
    fact_type = classify_adjustment_fact_type(score, f"{clean}{desc}")
    deduction_rules: list[str] = []
    if score < 0:
        deduction_rules = [rule] if rule else [f"最高扣{abs(score):g}分"]
    return _build_item(
        category=category,
        category_basis=category_basis,
        name=clean,
        description=desc,
        score_weight=max(score, 0.0),
        volume=volume,
        volume_label=refine_scoring_group_label(volume_label, clean),
        score_rules=[rule] if rule and score >= 0 else [],
        deduction_rules=deduction_rules,
        fact_type=fact_type,
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

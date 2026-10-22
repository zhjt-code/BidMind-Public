"""评分逻辑表装配器（根治）。

职责: 把评分区段内的表格行与失表页文本按"表头锚定+表尾封口"重建为归一化逻辑表段。
不做: 不判定整段是否评分表(detector 职责)、不提取评分项(parser 职责)、不调用 LLM/DB。

设计(对应 三条根因):
- 表头锚定: 全行扫描 is_scoring_header_row, 表头行开段, 重复表头(跨页续表)不切段,
 不同表头封口旧段——逻辑表边界由语义决定, 不再受"页连续×列数一致"物理假设摆布。
- 宽度组双向验证: 表头所在宽度组按表头位置定列角色; 其他宽度组(pdfplumber 列数抖动)
 按行分布推断(column_role_inference); 表头声明的分值列须有行分布支持, 否则回退推断。
- 接缝修复: 空名有分行从因素列 forward-fill(合并单元格续行); 失表页文本挂为待修复
 续行, 含显式"本项/共N项满分M分"声明时合成评分行, 否则整块丢弃不粘连。

渲染双轨: 未发生修复的单宽度段按原始行序渲染(与既有 parser 行为完全等价);
发生修复(多宽度/失表页桥接/forward-fill/声明合成)的段渲染为 canonical 四列表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.extractor.scoring.column_role_inference import (
    ColumnRoles,
    infer_column_roles,
)
from app.core.extractor.scoring.numeric_score_column_parser import is_score_subtotal_row
from app.core.extractor.scoring.score_column_semantics import (
    is_price_formula_factor,
    is_scoring_header_row,
    is_total_row,
    normalize_cell,
    parse_short_score,
    row_text,
)
from app.core.extractor.scoring.scoring_item_naming import recover_subitem_name_from_rule
from app.core.parser.scoring_header_role_binding import (
    HeaderBindingContext,
    bind_header_width_roles,
)
from app.core.parser.scoring_header_role_binding import (
    roles_from_header as _roles_from_header,
)
from app.core.parser.scoring_row_projection import project_scoring_row
from app.core.parser.scoring_sparse_assembly_gate import apply_sparse_projection
from app.core.parser.scoring_sparse_grid_projection import SparseGridProjectionSchema
from app.core.parser.table_grid_model import (
    ColumnStructureEvidence,
    extract_column_structure,
)
from app.core.parser.table_linearizer import join_cell_lines_cjk_aware

if TYPE_CHECKING:
    from app.core.parser.pdf_parser import ParsedDocument

# 表头前可保留的说明行(卷标签/分值构成), 供 parser 卷归属与组基准解析
_PRELUDE_KEEP_RE = re.compile(r"(分值构成|总分|报价部分|价格部分|商务部分|技术部分|综合部分|资信部分|服务部分)")
_PRELUDE_LOOKBACK = 8
# 切段时可剥离的段尾组头——单格短行 + 卷名信号(含「权重N%」形态)
_PRELUDE_GROUP_MAX_LEN = 30


def _is_peelable_prelude_group_row(row: list[str]) -> bool:
    """行是否为可剥离到下一段 prelude 的卷/权重组头(单格短文本)。"""
    non_empty = [str(c or "").strip() for c in row if str(c or "").strip()]
    if len(non_empty) != 1:
        return False
    compact = re.sub(r"\s+", "", non_empty[0])
    if not compact or len(compact) > _PRELUDE_GROUP_MAX_LEN:
        return False
    return bool(_PRELUDE_KEEP_RE.search(compact))

# 失表页续行显式满分声明: "本项满分N分" / "共三项满分N分"(普通规则文本中的"满分N分"不算)
_DECLARED_SCORE_RE = re.compile(
    r"(?:本项|共\s*[一二三四五六七八九十两0-9]{1,3}\s*项)[^。；;]*?满分\s*(\d+(?:\.\d+)?)\s*分"
)
_SCORE_ACTION_TERMS = ("得", "扣", "不得分", "打分", "评分")
_SYNTH_NAME_RE = re.compile(r"([\u4e00-\u9fff0-9A-Za-z]{2,20})[：:]")
_SYNTH_NAME_FALLBACK = "跨页评分项"
# 声明句附近才可信的标签窗口(字符); 远处的冒号标签多为无关小节标题
_SYNTH_NAME_WINDOW = 60
_SYNTH_NAME_SKIP_TERMS = ("注", "说明", "如下", "要求")


@dataclass
class CanonicalScoringRow:
    """归一化评分行(分组/因素/规则/分值)。"""

    group: str
    factor: str
    rule: str
    score_text: str
    score: float | None
    page: int
    # 规则列槽被合并单元格覆盖(cell_grid None槽)的物理证据,
    # 供组内共享规则继承判定; 无坐标数据的行为 False(不触发继承)。
    rule_slot_merged: bool = False


@dataclass
class LogicalScoringSegment:
    """表头锚定的逻辑表段。"""

    title: str
    header_row: list[str]
    rows: list[CanonicalScoringRow] = field(default_factory=list)
    raw_rows: list[list[str]] = field(default_factory=list)
    raw_row_pages: list[int | None] = field(default_factory=list)
    prelude_texts: list[str] = field(default_factory=list)
    prelude_rows: list[list[str]] = field(default_factory=list)
    page_start: int = 0
    page_end: int = 0
    repair_applied: bool = False
    # 表头宽度组经数据资格赛后的最终列角色(原表列序), 供载荷透传诊断
    resolved_roles: ColumnRoles | None = None
    # 实际贡献本段的物理表来源键集合; 供 collect_assembled_scoring_chunks
    # 在 chunk 验收后按段提交 claimed_global（拒绝段不 claim，防 fallback 遗漏）。
    source_keys: set[tuple[int, int]] = field(default_factory=set)

    @property
    def scored_row_count(self) -> int:
        return sum(1 for r in self.rows if r.score is not None)


def assemble_scoring_segments(
    parsed_doc: "ParsedDocument",
    section,
    *,
    exclude: set[tuple[int, int]] | None = None,
) -> tuple[list[LogicalScoringSegment], set[tuple[int, int]]]:
    """把单个评分区段重建为逻辑表段; 返回(段列表, 装配器认领物理表键集合)。

 返回的 claimed 集合仅供审计/诊断用途，调用方禁止直接合并至 ScoringChunkBuildState.claimed_global；
 全局 claim 更新须通过 compute_span_claim 按 span 级别的验收结果决策（C2）。

 exclude: 先前区段已认领的物理表键(page, 页内枚举位), 重叠区段(结构定位与
 表信号回扫常有交集)对同一张表只装配一次, 防重复 chunk。
 """
    builder = _SegmentStreamBuilder(section_title=getattr(section, "title", "") or "评分表")
    excluded = exclude or set()
    pages_by_num = {p.page_num: p for p in parsed_doc.pages}
    for page_num in range(section.page_start, section.page_end + 1):
        page = pages_by_num.get(page_num)
        if page is None:
            continue
        tables = list(getattr(page, "tables", None) or [])
        if tables:
            # 认领键用页内枚举位(与 builder 的 legacy 剔除口径一致), 不依赖 table_index 属性
            for pos, table in enumerate(tables):
                if (page_num, pos) in excluded:
                    continue
                if getattr(table, "data", None):
                    builder.feed_table(
                        page_num,
                        pos,
                        table.data,
                        cell_grid=getattr(table, "cell_grid", None),
                    )
        else:
            builder.feed_gap_text(page_num, getattr(page, "text", "") or "")
    return builder.finish()


class _SegmentStreamBuilder:
    """按事件流(表行/失表页文本)驱动的段构建状态机。"""

    def __init__(self, section_title: str) -> None:
        self.section_title = section_title
        self.segments: list[LogicalScoringSegment] = []
        self.claimed: set[tuple[int, int]] = set()
        self.current: LogicalScoringSegment | None = None
        # 当前段各"源表宽度组"的原始行(供列角色推断)与角色缓存
        self._group_rows: dict[int, list[list[str]]] = {}
        self._group_roles: dict[int, ColumnRoles | None] = {}
        self._header_width: int = 0
        self._header_roles: ColumnRoles | None = None
        self._weight_col: int | None = None
        self._clause_col: int | None = None
        # 表头所在物理表的列结构证据(跨列父表头/rowspan分组), 无坐标数据为 None
        self._header_structure: ColumnStructureEvidence | None = None
        # 当前 feed_table 表的结构证据暂存(表头行触发 _close_current 会重置
        # _header_structure, 须在绑定表头时从暂存移交, 防新表证据被段封口误清)
        self._pending_structure: ColumnStructureEvidence | None = None
        # 状态: 分组/因素继承 + 失表页待修复缓冲
        self._last_group = ""
        self._last_factor = ""
        self._pending: list[tuple[str, int, str]] = []  # (kind: gap|cont, page, text)
        self._lookback: list[tuple[int, list[str]]] = []  # 表头前行缓冲(page, row)
        self._segment_has_data = False
        # 两遍处理: 先缓存本段全部原始行(带宽度), 段封口时统一解析
        self._staged: list[tuple[str, int, int, object]] = []  # (kind, page, width, payload)
        # 归档 /Task4: 本段已确认的稀疏版式，供跨页续表复用列边界
        self._sparse_schema: SparseGridProjectionSchema | None = None

    # ------------------------------------------------------------------
    # 事件入口
    # ------------------------------------------------------------------

    def feed_table(
        self,
        page_num: int,
        table_index: int,
        data: list[list],
        *,
        cell_grid: list | None = None,
    ) -> None:
        # 与行对齐提取"None槽列集"(合并单元格覆盖证据), 供规则列rowspan继承
        grid_aligned = cell_grid is not None and len(cell_grid) == len(data)
        rows: list[list[str]] = []
        none_slots: list[frozenset[int]] = []
        for idx, raw_row in enumerate(data):
            if raw_row is None:
                continue
            rows.append([_cell_text(c) for c in raw_row])
            slots = cell_grid[idx] if grid_aligned else None
            none_slots.append(
                frozenset(i for i, slot in enumerate(slots) if slot is None)
                if slots
                else frozenset()
            )
        if not rows:
            return
        width = max(len(r) for r in rows)
        # 归档 /Task4: 超宽稀疏表按零跨越几何投影重排列
        sparse = apply_sparse_projection(
            data, cell_grid, rows, none_slots, width, self._sparse_schema
        )
        if sparse.claim_without_rows:
            self.claimed.add((page_num, table_index))
            return
        rows, none_slots, width = sparse.rows, sparse.none_slots, sparse.width
        # 表尾封口: 新物理表既无表头签名又不像当前逻辑表的续表(1519 权重汇总表),
        # 先封口旧段, 该表整体交 legacy 路径, 防止异质行被吸入明细段。
        if self.current is not None and not self._table_continues(rows, width):
            self._close_current()
        # 本表含表头行时提取列结构物理证据(跨列父表头词面不得绑定单列角色)。
        # 存入暂存位: _on_header_row 内部封口旧段会重置 _header_structure,
        # 证据须在 _bind_header_roles 时从暂存移交, 防被段封口误清。
        header_idx = next(
            (idx for idx, row in enumerate(rows) if is_scoring_header_row(row)), None
        )
        if header_idx is not None:
            self._pending_structure = extract_column_structure(
                rows, cell_grid, header_idx
            )
        table_claimed = False
        for row, row_none_cols in zip(rows, none_slots):
            if is_scoring_header_row(row):
                # C2: 若本表在此表头之前已向当前段贡献数据行或表头，
                # 提前将本表键记入该段，防止 _on_header_row 关闭段后键丢失。
                if self.current is not None and table_claimed:
                    self.current.source_keys.add((page_num, table_index))
                self._on_header_row(page_num, row)
                table_claimed = True
                continue
            if self.current is None:
                self._push_lookback(page_num, row)
                continue
            self._staged.append(("row", page_num, width, (row, row_none_cols)))
            table_claimed = True
        if table_claimed:
            self.claimed.add((page_num, table_index))
            # 仅认领表的 schema 可留存: 未认领表不代表本段版式（归档 /Task4）
            self._sparse_schema = sparse.schema
            # 记录来源键到当前段，供 compute_span_claim 按段做 claim 决策
            if self.current is not None:
                self.current.source_keys.add((page_num, table_index))

    def _table_continues(self, rows: list[list[str]], width: int) -> bool:
        """新物理表是否为当前逻辑表的续表。

        判据: 含表头签名行(交表头处理决断) / 与表头同宽(同结构跨页) /
        首行空名前导(合并单元格续行, 1547 的 6 列续表)。
        """
        if any(is_scoring_header_row(row) for row in rows):
            return True
        if width == self._header_width:
            return True
        first = rows[0]
        first_filled = next((i for i, c in enumerate(first) if (c or "").strip()), None)
        return first_filled is not None and first_filled >= 1

    def feed_gap_text(self, page_num: int, text: str) -> None:
        if self.current is None:
            return
        compact = (text or "").strip()
        if compact:
            self._staged.append(("gap", page_num, 0, compact))

    def finish(self) -> tuple[list[LogicalScoringSegment], set[tuple[int, int]]]:
        self._close_current()
        return self.segments, self.claimed

    # ------------------------------------------------------------------
    # 段生命周期
    # ------------------------------------------------------------------

    def _on_header_row(self, page_num: int, row: list[str]) -> None:
        if self.current is not None and not self._staged:
            # 连续表头(分组表头+真实列头): 合并, 新表头优先、空位由旧表头补
            merged = _merge_header_rows(self.current.header_row, row)
            self.current.header_row = merged
            self._bind_header_roles(merged)
            return
        if self.current is not None:
            if _norm_row(row) == _norm_row(self.current.header_row):
                return  # 跨页重复表头, 同一逻辑表继续
            # 混排表符合性段尾常挂「技术部分，权重N%」组头; 若直接封口,
            # 组头会困在上一段 raw 里。须在 _close_current 清空 lookback 之前取出,
            # 封口后再写入下一段 prelude。
            peeled = self._take_trailing_prelude_rows()
            self._close_current()
            self.current = LogicalScoringSegment(
                title=self.section_title,
                header_row=list(row),
                page_start=page_num,
                page_end=page_num,
            )
            for peel_page, peel_row in peeled:
                self._push_lookback(peel_page, peel_row)
            self._adopt_prelude()
            self._bind_header_roles(row)
            return
        self.current = LogicalScoringSegment(
            title=self.section_title,
            header_row=list(row),
            page_start=page_num,
            page_end=page_num,
        )
        self._adopt_prelude()
        self._bind_header_roles(row)

    def _take_trailing_prelude_rows(self) -> list[tuple[int, list[str]]]:
        """取出段尾可作 prelude 的单格卷/权重组头(时间正序); 从 staged 移除。

        职责: 仅剥离末尾连续短组头行。
        不做: 不改写已投影行、不跨非组头内容回捞、不推断分值。
        """
        peeled_rev: list[tuple[int, list[str]]] = []
        while self._staged:
            kind, page, _width, payload = self._staged[-1]
            if kind != "row":
                break
            staged_row, _none_cols = payload  # type: ignore[misc]
            if not _is_peelable_prelude_group_row(staged_row):
                break
            self._staged.pop()
            peeled_rev.append((page, list(staged_row)))
        return list(reversed(peeled_rev))

    def _close_current(self) -> None:
        seg = self.current
        self.current = None
        self._lookback = []
        self._header_structure = None
        self._sparse_schema = None  # 段封口重置，禁止版式跨段泄漏
        if seg is None:
            self._staged = []
            return
        self._resolve_group_roles(seg)
        self._replay_staged(seg)
        self._flush_pending(seg, next_row=None)
        _inherit_group_shared_rules(seg)
        # 零分值段(嵌入式括号分值/符合性表)也保留 raw 行, 交 builder 的 detector 决断,
        # 不得静默丢弃——否则 1521 型嵌入式表被认领后会整表消失(既有能力回退)。
        if seg.rows or seg.raw_rows or seg.prelude_rows:
            self.segments.append(seg)

    def _adopt_prelude(self) -> None:
        assert self.current is not None
        for page, row in self._lookback:
            if _PRELUDE_KEEP_RE.search(row_text(row)):
                self.current.prelude_rows.append(list(row))
                self.current.prelude_texts.append(" ".join(c for c in row if c).strip())
                self.current.page_start = min(self.current.page_start, page)
        self._lookback = []

    def _push_lookback(self, page_num: int, row: list[str]) -> None:
        self._lookback.append((page_num, row))
        if len(self._lookback) > _PRELUDE_LOOKBACK:
            self._lookback.pop(0)

    # ------------------------------------------------------------------
    # 列角色: 表头组按表头定位, 其他宽度组按分布推断(双向验证)
    # ------------------------------------------------------------------

    def _bind_header_roles(self, header: list[str]) -> None:
        self._header_width = len(header)
        roles, weight_col, clause_col = _roles_from_header(header)
        # 结构证据(跨列父表头/rowspan分组)仅作诊断辅助——无条件按 spanning
        # 解绑词面会误伤"父表头跨列但数据实际承载在首列"的常见形态(样本4 评审因素
        # 横跨3列, 对象名在col0); 词面绑定的纠偏统一交 _resolve_group_roles 的
        # 数据资格赛(词面必须经行分布确认), 结构证据随告警输出辅助人工定位。
        if self._pending_structure is not None:
            self._header_structure = self._pending_structure
            self._pending_structure = None
        self._header_roles = roles
        self._weight_col = weight_col
        self._clause_col = clause_col

    def _resolve_group_roles(self, seg: LogicalScoringSegment) -> None:
        self._group_rows = {}
        for kind, _page, width, payload in self._staged:
            if kind == "row":
                row, _none_cols = payload  # type: ignore[misc]
                self._group_rows.setdefault(width, []).append(row)
        self._group_roles = {}
        for width, rows in self._group_rows.items():
            if width == self._header_width and self._header_roles is not None:
                roles = self._header_roles
                # 双向验证与两条质量修正通路收口于 scoring_header_role_binding
                # （归档 /Task2；assembler 已达 Hard 阈值，不得在此堆逻辑）。
                score_supported = roles.score is not None and _column_has_short_score(
                    rows, roles.score
                )
                resolution = bind_header_width_roles(
                    rows, seg.header_row, roles, score_supported,
                    HeaderBindingContext(
                        page_start=seg.page_start,
                        page_end=seg.page_end,
                        header_structure=self._header_structure,
                    ),
                )
                if resolution.repair_forced:
                    seg.repair_applied = True
                seg.resolved_roles = resolution.resolved_roles
                # canonical_roles 仅更新 _group_roles（canonical 渲染路径），
                # 不写 resolved_roles——两路径分离避免 by_volume 失准。
                self._group_roles[width] = (
                    resolution.canonical_roles or resolution.resolved_roles
                )
                continue
            self._group_roles[width] = infer_column_roles(rows, min_score_rows=2)
        if len([w for w in self._group_rows if self._group_rows[w]]) > 1:
            seg.repair_applied = True

    # ------------------------------------------------------------------
    # 行重放: 原始行序 -> canonical 行 + 接缝修复
    # ------------------------------------------------------------------

    def _replay_staged(self, seg: LogicalScoringSegment) -> None:
        self._last_group = ""
        self._last_factor = ""
        self._pending = []
        staged, self._staged = self._staged, []
        for kind, page, width, payload in staged:
            if kind == "gap":
                self._pending.append(("gap", page, str(payload)))
                seg.repair_applied = True
                continue
            row, none_cols = payload  # type: ignore[misc]
            seg.raw_rows.append(list(row))
            seg.raw_row_pages.append(page)
            seg.page_end = max(seg.page_end, page)
            if is_total_row(row):
                continue
            roles = self._group_roles.get(width)
            if roles is None:
                projected = project_scoring_row(list(row))
                if projected is not None:
                    seg.repair_applied = True
                    seg.rows.append(
                        CanonicalScoringRow(
                            group=self._last_group,
                            factor=projected.name,
                            rule=projected.rule,
                            score_text=projected.score_text,
                            score=projected.score,
                            page=page,
                        )
                    )
                # 该宽度组若无可投影评分行, 视为资源/说明表混入: 丢弃, 禁止污染评分行
                continue
            self._consume_row(seg, page, row, roles, none_cols=none_cols)

    @staticmethod
    def _disambiguate_sibling_name(seg: LogicalScoringSegment, base: str) -> str:
        """同段已有同名行时追加兄弟序号（单一权威源, 空名与显式名两路径共用）。"""
        siblings = sum(
            1 for r in seg.rows if r.factor == base or r.factor.startswith(f"{base}(")
        )
        return f"{base}({siblings + 1})" if siblings else base

    def _consume_row(
        self,
        seg: LogicalScoringSegment,
        page: int,
        row: list[str],
        roles: ColumnRoles,
        *,
        none_cols: frozenset[int] = frozenset(),
    ) -> None:
        group_val = normalize_cell(_at(row, roles.group)) if roles.group is not None else ""
        factor_val = normalize_cell(_at(row, roles.name))
        score_text = _at(row, roles.score) if roles.score is not None else ""
        rule_val = _at(row, roles.rule)
        score = parse_short_score(score_text)
        displaced_rule = ""
        if score is None and self._weight_col is not None and len(row) == self._header_width:
            # 复杂表满分落在父级"权重分值"列(同源), 仅严格短分值可用
            fallback = parse_short_score(_at(row, self._weight_col))
            if fallback is not None and factor_val:
                # 位移布局下原分值列承载的长文本是评审规则, 不得因
                # roles.score 被 leftover 排除而静默丢弃(1809"类似项目业绩"实测)
                if score_text:
                    displaced_rule = score_text
                score, score_text = fallback, _at(row, self._weight_col)

        if group_val:
            self._last_group = group_val
        if (
            score is not None
            and group_val
            and is_price_formula_factor(group_val, factor_val)
        ):
            rule_val = " ".join(part for part in (factor_val, rule_val) if part).strip()
            factor_val = group_val
        if score is not None and factor_val and is_score_subtotal_row(factor_val, rule_val):
            self._last_group = factor_val
            return
        if score is None:
            # 无分值行: 续行。有待修复缓冲时归入缓冲(可能属失表行), 否则并回上一行。
            # 注意: 表内普通续行是常态而非"修复", 不得据此切换 canonical 渲染
            # (raw 渲染下既有 parser 原生处理续行, canonical 反而受列映射质量约束)。
            text = " ".join(part for part in (factor_val, rule_val) if part).strip()
            if not text:
                return
            if self._pending:
                self._pending.append(("cont", page, text))
            elif seg.rows:
                seg.rows[-1].rule = (seg.rows[-1].rule + " " + text).strip()
            else:
                self._pending.append(("cont", page, text))
            return

        self._flush_pending(seg, next_row=row)
        if not factor_val:
            if not rule_val:
                return
            # 空名计分行优先用规则首部的原文子项标签("1.印刷服务方案：…"),
            # 优于 forward-fill 父名或占位符——名称必须回到原文(证据忠实)。
            recovered = recover_subitem_name_from_rule(rule_val)
            base = recovered or self._last_factor or _SYNTH_NAME_FALLBACK
            # 合并单元格下的同名兄弟子行(金额业绩/数量业绩)用序号消歧, 防止
            # 身份键(卷+页+满分+名)碰撞被表内精确去重误并(边界)。
            factor_val = self._disambiguate_sibling_name(seg, base)
            seg.repair_applied = True  # 接缝空名 forward-fill
        else:
            self._last_factor = factor_val
            # 显式名同样须消歧——跨页续表会把父名再次写进名称格(sample23 p4
            # `应急预案`), 与前页恢复名撞成完全同名行; 兄弟计数随之从(3)起跳, 且身份键
            # 碰撞风险与空名路径同源。_last_factor 保留基名, 后续空名行仍按基名续号。
            factor_val = self._disambiguate_sibling_name(seg, factor_val)
        leftovers = _leftover_text(
            row,
            exclude={roles.group, roles.name, roles.score, roles.rule,
                     self._weight_col if len(row) == self._header_width else None,
                     self._clause_col if len(row) == self._header_width else None},
        )
        rule = " ".join(part for part in (rule_val, displaced_rule, leftovers) if part).strip()
        seg.rows.append(
            CanonicalScoringRow(
                group=self._last_group,
                factor=factor_val,
                rule=rule,
                score_text=score_text,
                score=score,
                page=page,
                rule_slot_merged=(roles.rule is not None and roles.rule in none_cols),
            )
        )

    # ------------------------------------------------------------------
    # 待修复缓冲: 显式满分声明 -> 合成评分行; 否则丢弃失表页文本(不粘连)
    # ------------------------------------------------------------------

    def _flush_pending(self, seg: LogicalScoringSegment, next_row: list[str] | None) -> None:
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        has_gap = any(kind == "gap" for kind, _p, _t in pending)
        merged = " ".join(text for _k, _p, text in pending).strip()
        pages = [p for _k, p, _t in pending]
        if not has_gap:
            # 纯表内续行(无失表页): 维持既有语义, 并回上一行
            if seg.rows and merged:
                seg.rows[-1].rule = (seg.rows[-1].rule + " " + merged).strip()
            return
        if next_row is None:
            return  # 段尾失表页散文(后续无表格行跟随): 丢弃, 禁止声明合成
        declared = _declared_score(merged)
        if declared is None or not any(t in merged for t in _SCORE_ACTION_TERMS):
            return  # 无显式声明: 整块丢弃, 不得粘进上一行(防污染)
        seg.repair_applied = True
        seg.page_end = max([seg.page_end, *pages])
        seg.rows.append(
            CanonicalScoringRow(
                group=self._last_group,
                factor=_synthesized_name(merged),
                rule=merged,
                score_text=f"{declared:g}",
                score=declared,
                page=pages[0],
            )
        )


def _inherit_group_shared_rules(seg: LogicalScoringSegment) -> None:
    """组内合并单元格共享规则继承。

 职责: 分组内规则文本唯一且存在"规则列槽被合并覆盖"的物理证据(cell_grid None槽)时,
 将该共享规则回填到组内规则为空的评分行(含跨页续行的空规则格)。
 不做: 未分组行不继承(防跨项错误归属); 组内多条规则不继承(逐项规则形态);
 未走 canonical 渲染的原样段不改写(raw 路径由既有 parser 消费)。
 """
    if not seg.repair_applied or not seg.rows:
        return
    by_group: dict[str, list[CanonicalScoringRow]] = {}
    for row in seg.rows:
        by_group.setdefault(row.group, []).append(row)
    for group, rows in by_group.items():
        if not group:
            continue
        ruled = [r for r in rows if r.rule.strip()]
        distinct_rules = {re.sub(r"\s+", "", r.rule) for r in ruled}
        has_merge_evidence = any(r.rule_slot_merged for r in rows)
        if len(distinct_rules) != 1 or not has_merge_evidence:
            continue
        shared_rule = ruled[0].rule
        for row in rows:
            if not row.rule.strip():
                row.rule = shared_rule


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def render_assembled_segment_markdown(seg: LogicalScoringSegment) -> str:
    """渲染逻辑表段: 修复段用 canonical 四列表, 原样段按原始行序(与既有链路等价)。"""
    lines = [f"[评分表 第{seg.page_start}-{seg.page_end}页]"]
    grid = segment_to_grid(seg)
    if not grid:
        return ""
    width = max(len(r) for r in grid)

    def _sanitize(cell: str) -> str:
        # 失表页正文可能含换行/竖线, 直拼会撕裂 markdown 表格行
        return join_cell_lines_cjk_aware(str(cell or "")).replace("|", "/").strip()

    def _fmt(row: list[str]) -> str:
        cells = [_sanitize(row[i] if i < len(row) else "") for i in range(width)]
        return "| " + " | ".join(cells) + " |"

    lines.append(_fmt(grid[0]))
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in grid[1:]:
        lines.append(_fmt(row))
    return "\n".join(lines)


def segment_to_grid(seg: LogicalScoringSegment) -> list[list[str]]:
    """段 -> 二维行(表头在首行), 供渲染与 detector 校验共用。

    修复段(多宽度合并/失表页桥接/forward-fill/声明合成)渲染为 canonical 四列表,
    分组变化以"单前导单元格标题行"形式显式输出, 供 parser 按行继承卷别;
    未修复且有评分行的段按原始行序渲染(与既有 stitcher 链路等价)。
    canonical 仅在确有评分行时使用, 零分值段(嵌入式括号分值等)保持原始行序,
    交既有 embedded/LLM 路径, 防能力回退。
    """
    if seg.repair_applied and seg.scored_row_count > 0:
        grid: list[list[str]] = [["分组", "评分因素", "评分内容与标准", "分值"]]
        for raw in seg.prelude_rows:
            grid.append(["", "", " ".join(c for c in raw if c).strip(), ""])
        current_group = ""
        for r in seg.rows:
            if r.group and r.group != current_group:
                current_group = r.group
                grid.append([r.group, "", "", ""])
            grid.append(["", r.factor, r.rule, r.score_text])
        return grid
    grid = [list(seg.header_row)]
    grid.extend(list(r) for r in seg.prelude_rows)
    grid.extend(list(r) for r in seg.raw_rows)
    return grid


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def _column_has_short_score(rows: list[list[str]], col: int) -> bool:
    return any(
        not is_total_row(row) and parse_short_score(_at(row, col)) is not None for row in rows
    )


def _declared_score(text: str) -> float | None:
    matches = list(_DECLARED_SCORE_RE.finditer(text or ""))
    if not matches:
        return None
    try:
        value = float(matches[-1].group(1))
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= 200 else None


def _synthesized_name(merged: str) -> str:
    """从合并续行文本中取声明句最近的短标签作合成行名, 无可信标签用固定兜底名。"""
    matches = list(_DECLARED_SCORE_RE.finditer(merged))
    cutoff = matches[-1].start() if matches else len(merged)
    labels = [
        (m.start(), m.group(1)) for m in _SYNTH_NAME_RE.finditer(merged[:cutoff])
    ]
    for start, label in reversed(labels):
        if cutoff - start > _SYNTH_NAME_WINDOW:
            break  # 标签离声明句过远, 多为演示内容小节标题而非评分对象名, 宁用兜底名
        name = re.sub(r"^[一二三四五六七八九十0-9]+[、.．]?", "", label).strip()
        if name.endswith("注"):
            continue  # "……注：" 是声明句前的注释引导词, 非评分对象名
        if 2 <= len(name) <= 20 and name not in _SYNTH_NAME_SKIP_TERMS:
            return name
    return _SYNTH_NAME_FALLBACK


def _leftover_text(row: list[str], *, exclude: set[int | None]) -> str:
    parts = []
    for idx, cell in enumerate(row):
        if idx in exclude:
            continue
        text = (cell or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def _at(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _cell_text(cell: object) -> str:
    return join_cell_lines_cjk_aware(str(cell or ""))


def _norm_row(row: list[str]) -> tuple[str, ...]:
    return tuple(normalize_cell(c) for c in row)


def _merge_header_rows(old: list[str], new: list[str]) -> list[str]:
    merged = list(new)
    for idx, cell in enumerate(old):
        if idx >= len(merged):
            merged.append(cell)
        elif not (merged[idx] or "").strip() and (cell or "").strip():
            merged[idx] = cell
    return merged


__all__ = [
    "CanonicalScoringRow",
    "LogicalScoringSegment",
    "assemble_scoring_segments",
    "render_assembled_segment_markdown",
    "segment_to_grid",
]

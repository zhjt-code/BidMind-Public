# -*- coding: utf-8 -*-
"""table_scope_resolution 单元级防御测试。

覆盖范围:
- _total_row_value: 负分识别(parse_adjustment_score 单源)、范围值解析(-10～2)
- resolve_table_scopes: I1 空壳跳过；declared<=0 跳闭合；declared>0 仍严格闭合
- _scope_title_from_page: I2 全 header 候选归一唯一性：多不同→None，唯一→返回
- _qualify_heading_line: I3 末段表结构说明拒绝回落首段；I4 精确标签断言

不做: 不读 DB/LLM/文件系统，所有输入为内联构造的纯 Python 数据。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

import pytest  # noqa: E402

from app.core.extractor.scoring.table_scope_resolution import (  # noqa: E402
    _qualify_heading_line,
    _total_row_value,
    resolve_table_scopes,
)

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 辅助: 极简网格构造
# ---------------------------------------------------------------------------

def _make_grid(header_row: list[str], data_rows: list[list[str]], total_row: list[str]) -> list[list[str]]:
    return [header_row] + data_rows + [total_row]


_HEADER = ["评审因素", "评分标准", "分值"]
_SCORE_COL = 2


# ===========================================================================
# T1: _total_row_value ── 负分/范围值识别
# ===========================================================================


class TestTotalRowValue:
    """_total_row_value 应通过 parse_adjustment_score 识别负合计行。"""

    def _row(self, score_cell: str) -> list[str]:
        return ["", "合计", score_cell]

    def test_positive_score_recognized(self):
        """正分值走原有路径仍正常识别。"""
        assert _total_row_value(self._row("100"), _SCORE_COL) == pytest.approx(100.0)

    def test_zero_recognized_as_adjustment(self):
        """0 分被 parse_adjustment_score 识别（恒0调整项）。"""
        val = _total_row_value(self._row("0"), _SCORE_COL)
        assert val == pytest.approx(0.0)

    def test_negative_integer_recognized(self):
        """负整数 -10 被 parse_adjustment_score 识别。"""
        val = _total_row_value(self._row("-10"), _SCORE_COL)
        assert val is not None
        assert val == pytest.approx(-10.0)

    def test_negative_with_fen_suffix(self):
        """-10分 (含'分'后缀) 识别。"""
        val = _total_row_value(self._row("-10分"), _SCORE_COL)
        assert val is not None
        assert val == pytest.approx(-10.0)

    def test_range_value_negative_lower_bound(self):
        """范围格式 '-10～2' 应取负下界 -10.0（精确断言）。"""
        val = _total_row_value(self._row("-10～2"), _SCORE_COL)
        assert val == pytest.approx(-10.0), (
            f"_total_row_value('-10～2') 应返回 -10.0, 实际: {val}"
        )

    def test_range_tilde_ascii(self):
        """ASCII 波浪号 '-10~2' 取负下界 -10.0（精确断言）。"""
        val = _total_row_value(self._row("-10~2"), _SCORE_COL)
        assert val == pytest.approx(-10.0), (
            f"_total_row_value('-10~2') 应返回 -10.0, 实际: {val}"
        )

    def test_range_positive_only_still_returns_max(self):
        """纯正区间 '0～30' 应通过 parse_score_range_max 取上界 30.0（精确断言）。"""
        val = _total_row_value(self._row("0～30"), _SCORE_COL)
        assert val == pytest.approx(30.0), (
            f"_total_row_value('0～30') 应返回 30.0, 实际: {val}"
        )

    def test_garbage_returns_none(self):
        """乱码单元格应返回 None。"""
        assert _total_row_value(self._row("N/A"), _SCORE_COL) is None

    def test_score_in_non_score_col_fallback(self):
        """分值列(col=2)为空时, 全行唯一数值格作为 fallback 取 100。"""
        row = ["100", "合计", ""]  # score_col=2 → cell="" → fallback 扫全行 → 100.0
        val = _total_row_value(row, _SCORE_COL)
        assert val == pytest.approx(100.0), (
            f"全行唯一数值 fallback 应返回 100.0, 实际: {val}"
        )


# ===========================================================================
# T2: resolve_table_scopes ── declared<=0 不做正分闭合
# ===========================================================================


class TestResolveScopesClosure:
    """declared>0 严格闭合；declared<=0 仅作边界，不用正分 sum 与负总比较。"""

    def _page_texts(self, title: str) -> dict[int, str]:
        """构造含表头行的简单页面文本。"""
        return {
            1: (
                f"{title}\n"
                "评审因素 评分标准 分值\n"
            )
        }

    def _row_pages(self, n_data: int) -> list[int]:
        # header(1页) + n_data 数据行 + 合计行
        return [1] * (1 + n_data + 1)

    def test_positive_scope_closes_correctly(self):
        """sum(data)==declared>0 → 作用域产出。"""
        grid = _make_grid(
            _HEADER,
            [["项目A", "规则", "60"], ["项目B", "规则", "40"]],
            ["", "合计", "100"],
        )
        pages = {1: "商务评审\n评审因素 评分标准 分值\n"}
        scopes = resolve_table_scopes(
            grid, [1] * len(grid), pages, score_col=_SCORE_COL
        )
        assert len(scopes) == 1
        assert scopes[0]["declared"] == pytest.approx(100.0)
        assert scopes[0]["label"] == "商务评审"

    def test_positive_scope_not_closed_rejected(self):
        """ 负例: sum(data) != declared>0 → 作用域拒绝(fail-closed)。"""
        grid = _make_grid(
            _HEADER,
            [["项目A", "规则", "50"], ["项目B", "规则", "40"]],  # sum=90, declared=100
            ["", "合计", "100"],
        )
        pages = {1: "商务评审\n评审因素 评分标准 分值\n"}
        scopes = resolve_table_scopes(
            grid, [1] * len(grid), pages, score_col=_SCORE_COL
        )
        assert len(scopes) == 0, "正分不闭合时作用域不应产出"

    def test_negative_declared_skips_closure_check(self):
        """: declared<=0 合计行作为边界，不检查 sum(正分)>=0 vs 负 declared。"""
        grid = _make_grid(
            _HEADER,
            [["智慧物流", "规则", "2"]],  # 正分 2.0
            ["", "合计", "-10"],  # 负合计 → 不应做闭合检查
        )
        pages = {1: "其他评审\n评审因素 评分标准 分值\n"}
        scopes = resolve_table_scopes(
            grid, [1] * len(grid), pages, score_col=_SCORE_COL
        )
        assert len(scopes) == 1, (
            "declared<=0 的作用域应当产出(跳过闭合检查), 实际产出 0 个"
        )
        assert scopes[0]["label"] == "其他评审"
        assert scopes[0]["declared"] == pytest.approx(-10.0)

    def test_negative_range_declared_skips_closure(self):
        """范围合计行 '-10～2' declared 识别后跳过闭合检查。"""
        grid = _make_grid(
            _HEADER,
            [["智慧物流", "规则", "2"]],
            ["", "合计", "-10～2"],
        )
        pages = {1: "其他评审\n评审因素 评分标准 分值\n"}
        scopes = resolve_table_scopes(
            grid, [1] * len(grid), pages, score_col=_SCORE_COL
        )
        assert len(scopes) == 1, (
            "'-10～2' 负范围合计行作用域应产出, 实际产出 0 个"
        )

    def test_zero_declared_skips_closure(self):
        """declared=0 也跳过闭合检查。"""
        grid = _make_grid(
            _HEADER,
            [["失信项", "规则", "0"]],
            ["", "合计", "0"],
        )
        pages = {1: "扣分审查\n评审因素 评分标准 分值\n"}
        scopes = resolve_table_scopes(
            grid, [1] * len(grid), pages, score_col=_SCORE_COL
        )
        assert len(scopes) == 1, "declared=0 作用域应产出"

    def test_two_scopes_positive_and_negative(self):
        """Minor: 正合计+负合计各自产出, 各用独立页面使标题无歧义(精确标签)。"""
        grid = [
            _HEADER,                              # row 0
            ["技术项A", "规则", "60"],            # row 1
            ["技术项B", "规则", "40"],            # row 2
            ["", "合计", "100"],                  # row 3: declared=100, sum=100 ✓
            ["其他项", "规则", "2"],              # row 4
            ["", "合计", "-10"],                  # row 5: declared=-10, 跳过闭合
        ]
        # 两个作用域分属不同页, 每页仅含一个 header+标题 → 无歧义
        row_pages = [1, 1, 1, 1, 2, 2]
        pages = {
            1: "技术评审\n评审因素 评分标准 分值\n",
            2: "其他评审\n评审因素 评分标准 分值\n",
        }
        scopes = resolve_table_scopes(grid, row_pages, pages, score_col=_SCORE_COL)
        assert len(scopes) == 2, f"期望2个作用域, 实际: {len(scopes)}"
        labels = {s["label"] for s in scopes}
        assert labels == {"技术评审", "其他评审"}, f"标签不符: {labels}"
        pos = next(s for s in scopes if s["declared"] > 0)
        assert pos["label"] == "技术评审"
        assert pos["declared"] == pytest.approx(100.0)
        neg = next(s for s in scopes if s["declared"] < 0)
        assert neg["label"] == "其他评审"
        assert neg["declared"] == pytest.approx(-10.0)

    def test_empty_member_rows_scope_skipped(self):
        """I1: 无数据行的空壳作用域应被跳过。

 两合计行相邻时第二作用域 member_rows=[], 不产出 scope。
 """
        grid = [
            _HEADER,
            ["项目A", "规则", "100"],
            ["", "合计", "100"],       # 第一作用域: sum=100, declared=100 ✓
            ["", "合计", "50"],        # 第二作用域: member_rows=[] → 跳过
        ]
        pages = {1: "商务评审\n评审因素 评分标准 分值\n"}
        scopes = resolve_table_scopes(
            grid, [1] * len(grid), pages, score_col=_SCORE_COL
        )
        assert len(scopes) == 1, (
            f"I1: 空壳作用域应被跳过, 实际产出 {len(scopes)} 个"
        )
        assert scopes[0]["declared"] == pytest.approx(100.0)
        assert scopes[0]["label"] == "商务评审"


# ===========================================================================
# T3: _scope_title_from_page ── 继续扫后续同 header
# ===========================================================================


class TestScopeTitleFromPage:
    """_scope_title_from_page: 首个 header 无合格标题时, 继续扫后续 header。"""

    _HEADER_COMPACT = "评审因素评分标准分值"  # compact 形式

    def test_title_found_at_first_header(self):
        """正常路径：首个 header 上方有合格标题。"""
        page_text = "商务评审\n评审因素 评分标准 分值\n项目A 规则 50\n"
        from app.core.extractor.scoring.table_scope_resolution import _scope_title_from_page
        result = _scope_title_from_page(page_text, self._HEADER_COMPACT)
        assert result == "商务评审"

    def test_first_header_no_title_second_header_has_title(self):
        """全扫描唯一性: 首 header 无合格标题, 第二 header 有标题 → 唯一候选 → 返回。

        sample26 的 '其他评审' scope 正是此情形(after_total_value=None, 首 header
        是前一 chunk 的延续续段无标题, 第二 header 才是其他评审表头)。
        全扫描收到 1 个候选 → 唯一 → 返回。
        """
        page_text = (
            "评审因素 评分标准 分值\n"
            "项目A 规则 50\n"
            "合计  50\n"
            "技术评审\n"
            "评审因素 评分标准 分值\n"
            "项目B 规则 50\n"
        )
        from app.core.extractor.scoring.table_scope_resolution import _scope_title_from_page
        result = _scope_title_from_page(page_text, self._HEADER_COMPACT)
        assert result == "技术评审", (
            f"全扫描唯一候选应返回 '技术评审', 实际: {result!r}"
        )

    def test_no_header_returns_none(self):
        """页面无 header 行 → None。"""
        from app.core.extractor.scoring.table_scope_resolution import _scope_title_from_page
        page_text = "技术评审\n项目A 规则 50\n合计 50\n"
        result = _scope_title_from_page(page_text, self._HEADER_COMPACT)
        assert result is None

    def test_no_anchor_conflicting_titles_fail_closed(self):
        """无锚点时同页两个同构表各有不同标题 → 候选集冲突 → fail-closed None。

        候选集 {"技术评审", "商务评审"} 有 2 个不同值 → 唯一性校验失败 → None。
        注: 若全页仅一个合格标题但来自无关同构表(无 table ordinal 区分),
        当前实现将返回该标题而非 None——这是已知边界(见函数 docstring)。
        """
        from app.core.extractor.scoring.table_scope_resolution import _scope_title_from_page
        page_text = (
            "技术评审\n"
            "评审因素 评分标准 分值\n"  # 首 header, 标题 "技术评审"
            "项目A 规则 50\n"
            "合计 50\n"
            "商务评审\n"               # 不同标题同构表
            "评审因素 评分标准 分值\n"
            "项目B 规则 30\n"
            "合计 30\n"
        )
        result = _scope_title_from_page(page_text, self._HEADER_COMPACT)
        assert result is None, (
            f"同页两同构表不同标题 → 候选冲突 → None, 实际: {result!r}"
        )

    def test_both_headers_have_no_title_returns_none(self):
        """两个 header 上方均无合格标题 → None。"""
        from app.core.extractor.scoring.table_scope_resolution import _scope_title_from_page
        page_text = (
            "评审因素 评分标准 分值\n"
            "合计 50\n"
            "评审因素 评分标准 分值\n"
            "合计 50\n"
        )
        result = _scope_title_from_page(page_text, self._HEADER_COMPACT)
        assert result is None

    def test_two_headers_different_titles_fail_closed(self):
        """I2(有锚点): 锚点后两个同构表不同标题 → fail-closed None(歧义不可信)。

        必须提供 after_total_value 才会走"多 header 候选唯一性扫描"路径。
        """
        from app.core.extractor.scoring.table_scope_resolution import _scope_title_from_page
        page_text = (
            "合计 100\n"               # 锚点
            "技术评审\n"
            "评审因素 评分标准 分值\n"  # 锚点后第一个 header
            "项目A 规则 50\n"
            "合计 50\n"
            "商务评审\n"
            "评审因素 评分标准 分值\n"  # 锚点后第二个 header
            "项目B 规则 50\n"
        )
        result = _scope_title_from_page(
            page_text, self._HEADER_COMPACT, after_total_value=100.0
        )
        assert result is None, (
            f"I2: 有锚点时两 header 不同标题应返回 None, 实际: {result!r}"
        )

    def test_two_headers_same_title_returns_it(self):
        """I2(有锚点): 锚点后两个同构表相同标题(多个相同可接受) → 返回该标题。"""
        from app.core.extractor.scoring.table_scope_resolution import _scope_title_from_page
        page_text = (
            "合计 100\n"               # 锚点
            "技术评审\n"
            "评审因素 评分标准 分值\n"  # 锚点后第一个 header
            "项目A 规则 50\n"
            "合计 50\n"
            "技术评审\n"
            "评审因素 评分标准 分值\n"  # 锚点后第二个 header
            "项目B 规则 50\n"
        )
        result = _scope_title_from_page(
            page_text, self._HEADER_COMPACT, after_total_value=100.0
        )
        assert result == "技术评审", (
            f"I2: 有锚点时两 header 相同标题应返回该标题, 实际: {result!r}"
        )

    def test_after_total_value_filters_first_header(self):
        """after_total_value 参数过滤首 header (属于上一表跨页续段)。"""
        from app.core.extractor.scoring.table_scope_resolution import _scope_title_from_page
        page_text = (
            "合计 100\n"                 # 上一作用域合计行
            "评审因素 评分标准 分值\n"    # 首 header (跨页续段, 应跳过)
            "项目A 规则 50\n"
            "技术评审\n"                 # 第二 header 前的标题
            "评审因素 评分标准 分值\n"
            "合计 50\n"
        )
        result = _scope_title_from_page(
            page_text, self._HEADER_COMPACT, after_total_value=100.0
        )
        assert result == "技术评审"

    def test_sample26_page_text_simulation(self):
        """模拟 sample26 其他评审 scope: after_total_value=None + I3 联动。

        实际处理中, '其他评审' scope 在独立 chunk 中, prev_total_page=None (首 scope),
        故 after_total_value=None。页面有两个同构 header: 第一个是前一 chunk 延续
        (无合格标题), 第二个才是其他评审表头(上方是粘连长行)。
        全扫描: 候选集 = {"其他评审"} → 唯一 → 返回。

        I3: 粘连长行末段 '其他评审的分值在总分值中为绝对分值,不进行加权' 含 '总分'
            → is_total_like_label 拒绝; 回落首段 '其他评审' → 通过 → 候选。
        """
        from app.core.extractor.scoring.table_scope_resolution import _scope_title_from_page
        page_text = (
            "评审因素 评分标准 分值\n"          # 第一 header (前一 chunk 延续, 无合格标题)
            "某项目 规则 5\n"
            "合计 5\n"
            "5.2.4 其他评审。其他评审的分值在总分值中为绝对分值,不进行加权。\n"
            "评审因素 评分标准 分值\n"          # 第二 header, 上方粘连长行
            "智慧物流 规则 2\n"
        )
        # after_total_value=None, 与实际 chunk 2 处理一致
        result = _scope_title_from_page(page_text, self._HEADER_COMPACT)
        assert result == "其他评审", (
            f"全扫描+I3: 应提取 '其他评审', 实际: {result!r}"
        )


# ===========================================================================
# T4: _qualify_heading_line ── 粘连长行句号分割
# ===========================================================================


class TestQualifyHeadingLine:
    """_qualify_heading_line 处理粘连长行: 中文句号切末段→验证→失败→回落首段。"""

    def test_simple_short_title(self):
        """简单标题直接通过验证。"""
        assert _qualify_heading_line("商务评审") == "商务评审"

    def test_clause_prefixed_title(self):
        """条款号前缀剥除后提取标题。"""
        assert _qualify_heading_line("5.2.1 商务评审") == "商务评审"

    def test_long_line_rejected(self):
        """超过24字的长行(无句号)被拒绝。"""
        long_line = "商务评审评审因素评审标准评定内容评定依据具体项目分值分配说明"
        result = _qualify_heading_line(long_line)
        assert result is None

    def test_concatenated_line_last_segment_table_desc_falls_back_to_first(self):
        """I3/I4: 末段为表格结构说明(含'评审因素及标准'等)→ 拒绝末段, 回落首段精确返回。

        '5.2.2 技术评审。评审因素及标准如下表所示。'
        末段: '评审因素及标准如下表所示' → 含 _TABLE_DESC_TERMS 关键词 → 拒绝
        首段(剥除条款号): '技术评审' → 通过 → 精确返回 '技术评审'
        """
        line = "5.2.2 技术评审。评审因素及标准如下表所示。"
        result = _qualify_heading_line(line)
        assert result == "技术评审", (
            f"I3/I4: 末段表结构说明应被拒绝, 回落首段返回 '技术评审', 实际: {result!r}"
        )

    def test_concatenated_line_last_segment_too_long_falls_back_to_first(self):
        """ 核心: 末段 >24 字时回落到首段(若首段合格)。

 '5.2.4 其他评审。其他评审的分值在总分值中为绝对分值,不进行加权。'
 末段: '其他评审的分值在总分值中为绝对分值,不进行加权' → 过长 → 拒绝
 首段(条款号剥除后): '其他评审' → 4字/4中文/无分值 → 通过
 """
        line = "5.2.4 其他评审。其他评审的分值在总分值中为绝对分值,不进行加权。"
        result = _qualify_heading_line(line)
        assert result == "其他评审", (
            f"末段过长应回落到首段 '其他评审', 实际: {result!r}"
        )

    def test_concatenated_line_last_segment_has_score_mention_falls_back(self):
        """末段含分值关键词→无效, 回落首段。

        '5.2.3 价格评审。满分100分,按报价最优标投标人得100分。'
        末段: '满分100分,按报价最优标投标人得100分' → 含 '分值' → 拒绝
        首段(剥除条款号): '价格评审' → 通过
        """
        line = "5.2.3 价格评审。满分100分,按报价最优标投标人得100分。"
        result = _qualify_heading_line(line)
        assert result == "价格评审", (
            f"末段含分值关键词应回落首段 '价格评审', 实际: {result!r}"
        )

    def test_long_body_text_no_valid_segment_returns_none(self):
        """ 负例: 正文长行无句号, 整行超24字 → 应返回 None。

 不得从正文中任取前段作为标题。
 """
        # 模拟一行普通正文, 无条款前缀, 无句号切分
        line = "根据招标文件要求投标人应当具备相应资质条件及业绩证明材料"
        result = _qualify_heading_line(line)
        assert result is None, f"长正文行不应产出标题, 实际: {result!r}"

    def test_last_segment_valid_first_segment_total_like(self):
        """末段通过验证时直接返回末段, 首段的合计类词无关。

        '合计总分如下。具体见附件说明。'
        末段: '具体见附件说明' → 7字/中文/无分值/非合计类 → 通过 → 返回
        首段 '合计总分如下' 含合计类词, 但末段已优先通过, 不需要回落首段。
        """
        line = "合计总分如下。具体见附件说明。"
        result = _qualify_heading_line(line)
        # 末段 '具体见附件说明' 通过所有门禁 → 返回它
        assert result == "具体见附件说明", (
            f"末段有效时应直接返回末段, 实际: {result!r}"
        )

    def test_score_mention_in_first_segment_second_segment_valid(self):
        """首段含分值词(被拒绝), 但按末段优先逻辑末段才是目标候选。

        '100分项目。商务评审'
        末段: '商务评审' → 4字, 无分值 → 通过
        (无需回落首段, 末段已通过)
        """
        line = "100分项目。商务评审"
        result = _qualify_heading_line(line)
        assert result == "商务评审", (
            f"末段 '商务评审' 有效, 应被返回, 实际: {result!r}"
        )

    def test_both_segments_invalid_returns_none(self):
        """末段和首段均无效 → None。"""
        # 末段: 超长; 首段: 含合计词
        line = "合计总分构成说明。根据招标文件要求投标人应当具备相应资质条件及业绩证明材料完整文件。"
        result = _qualify_heading_line(line)
        assert result is None

    def test_table_desc_term_biaozhun_ruhe_falls_back_to_first(self):
        """'标准如下'末段为表结构说明 → 拒绝末段, 回落首段精确返回。

        '5.2.2 技术评审。评审标准如下。'
        末段: '评审标准如下' → 含 '标准如下' → 表结构说明 → 拒绝
        首段(剥条款号): '技术评审' → 通过 → 精确返回 '技术评审'
        """
        line = "5.2.2 技术评审。评审标准如下。"
        result = _qualify_heading_line(line)
        assert result == "技术评审", (
            f"末段'标准如下'应被拒绝, 回落首段返回 '技术评审', 实际: {result!r}"
        )

    def test_table_desc_term_guiding_ruhe_falls_back_to_first(self):
        """'规定如下'末段为表结构说明 → 拒绝末段, 回落首段精确返回。

        '5.2.3 报价评审。评分规定如下。'
        末段: '评分规定如下' → 含 '规定如下' → 表结构说明 → 拒绝
        首段(剥条款号): '报价评审' → 通过 → 精确返回 '报价评审'
        """
        line = "5.2.3 报价评审。评分规定如下。"
        result = _qualify_heading_line(line)
        assert result == "报价评审", (
            f"末段'规定如下'应被拒绝, 回落首段返回 '报价评审', 实际: {result!r}"
        )

"""声明捕获模块防御测试（换轴重构 P1 A组）。

职责: 验证 capture_scoring_declarations 的捕获口径(锚点/平铺/列举/折算/扩展形态/
 总分候选)、 负向门控与同标签冲突拒用、权重附加与去重。
不做: 不读 fixture 语料、不读任何 holdout 真值文件、不触发 LLM/DB。
"""
from __future__ import annotations

import pytest

from app.core.extractor.scoring.ledger.contracts import LedgerCapture
from app.core.extractor.scoring.ledger.declaration_capture import (
    _ledger_flat_pairs,
    capture_scoring_declarations,
    declaration_pages_from_signals,
)

pytestmark = pytest.mark.scoring


def _capture_text(*lines: str) -> LedgerCapture:
    """把若干行文本作为单页(页码1)送入捕获, 不带表格。"""
    return capture_scoring_declarations([(1, "\n".join(lines))], [])


def test_item_paren_score_cap_not_captured_bug720():
    """ 负向: "项目负责人配备标准(7分)"是单项自身分值上限, 整行不入组对。"""
    capture = _capture_text("项目负责人配备标准(7分)")
    assert capture.pairs == ()
    assert "ext_item_standard_excluded=1" in capture.diagnostics


def test_item_level_paren_line_gated_by_group_suffix():
    """组尾缀门控: "质量保证措施（15分）"项级行不入组对, 且计入门控排除诊断。"""
    capture = _capture_text("质量保证措施（15分）")
    assert capture.pairs == ()
    assert "ext_suffix_gate_excluded=1" in capture.diagnostics


def test_group_standard_form_paren_still_captured():
    """门控精度: "商务评分标准（20分）"是组语义标准形态, 必须正常捕获。"""
    capture = _capture_text("商务评分标准（20分）")
    assert [(p.norm_label, p.score, p.source) for p in capture.pairs] == [("商务", 20.0, "group_paren")]


def test_occupation_share_pairs_captured_with_marker_normalization():
    """ doc2478 实测(全角冒号形态): "X：N分"权重构成行捕获为组对,
 编号标记归一剥离; "占"连接符变体同捕获。"""
    capture = _capture_text(
        "|  | 2）评分因素权重 | 磋商报价部分F1：20分、 技术、商务部分F2：80分。 |"
    )
    assert [(p.norm_label, p.score, p.source) for p in capture.pairs] == [
        ("磋商报价", 20.0, "occupation_share"),
        ("技术、商务", 80.0, "occupation_share"),
    ]
    occupy = _capture_text(
        "2）评分因素权重 磋商报价部分F1占20分、 技术、商务部分F2占80分。"
    )
    assert [(p.norm_label, p.score) for p in occupy.pairs] == [
        ("磋商报价", 20.0),
        ("技术、商务", 80.0),
    ]


def test_occupation_share_requires_weight_trigger_and_closure():
    """fail-closed 三闸: 无"权重"语境 / 单对 / 合计偏离100 均不入账。"""
    no_trigger = _capture_text("磋商报价部分F1占20分、技术、商务部分F2占80分。")
    assert all(p.source != "occupation_share" for p in no_trigger.pairs)
    single = _capture_text("评分因素权重 服务响应占10分即可得分")
    assert all(p.source != "occupation_share" for p in single.pairs)
    unclosed = _capture_text("评分因素权重 商务占20分、技术占50分。")
    assert all(p.source != "occupation_share" for p in unclosed.pairs)


def test_marker_tail_normalization_aligns_declaration_and_item_labels():
    """: 声明侧"部分F1"与提取侧"评审（F1）"归一化后必须同键；
 "采购包1"类 CJK+数字实义标签不得被剥。"""
    from app.core.extractor.scoring.ledger.label_normalization import (
        normalize_group_label,
    )

    assert normalize_group_label("磋商报价部分F1") == normalize_group_label(
        "磋商报价评审（F1）"
    ) == "磋商报价"
    assert normalize_group_label("技术、商务部分F2") == normalize_group_label(
        "技术、商务评审（F2）"
    ) == "技术、商务"
    assert normalize_group_label("采购包1") == "采购包1"


def test_same_label_conflict_rejected_fail_visible():
    """ 冲突拒用: 同标签不同分值(20 vs 30)两条都不进 pairs, conflicts 非空。"""
    capture = _capture_text(
        "分值构成：商务部分：20分；技术部分：80分",
        "分值构成：商务部分：30分；技术部分：80分",
    )
    assert {p.norm_label for p in capture.pairs} == {"技术"}
    assert len(capture.conflicts) == 1
    assert "商务" in capture.conflicts[0] and "" in capture.conflicts[0]


def test_conflict_explained_by_weight_is_kept():
    """同标签双分值可由权重解释(100x0.4=40)时不判冲突, 两条声明均保留。"""
    capture = _capture_text(
        "技术标总分100分，按40%权重折算",
        "分值构成：技术部分：40分；商务部分：60分",
    )
    assert capture.conflicts == ()
    tech_scores = {p.score for p in capture.pairs if p.norm_label == "技术"}
    assert tech_scores == {100.0, 40.0}


def test_weighted_def_pair_with_weight():
    """折算卷定义: "技术标总分100分…按40%权重折算" → weighted_def 对带 weight=0.4。"""
    capture = _capture_text("技术标总分100分，按40%权重折算")
    assert [(p.norm_label, p.score, p.weight, p.source) for p in capture.pairs] == [
        ("技术", 100.0, 0.4, "weighted_def")
    ]


def test_supplementary_declaration_pages_contribute_group_pairs():
    """文档级声明补充页必须能提供组声明对（输入范围缺口）。

 项目2476 实测: 权重声明"满分均为100…分别占30%、15%、55%"写在正文段落, 而账本
 原本只读 scoring_table chunk 正文, 该声明在 chunk 内不存在 -> 恒落 L4 无基准。
 声明本身已由 scoring_signals 捕获, 只是没喂给账本。
 """
    capture = capture_scoring_declarations(
        [(1, "| 分组 | 评分因素 | 分值 |")],
        [],
        supplementary_pages=[
            (
                6,
                "(2)评分细则按技术标、资信标、商务标分别编写,满分均为100,"
                "按技术标、资信标、商务标分别占30%、 15%、55%的权重计算最终评标得分",
            )
        ],
    )

    shares = [(p.norm_label, p.score, p.weight) for p in capture.pairs]
    assert shares == [("技术", 100.0, 0.3), ("资信", 100.0, 0.15), ("商务", 100.0, 0.55)]


def test_supplementary_pages_never_contribute_total_candidates():
    """安全边界(教训): 补充页只提供组声明, 绝不提供文档级总分候选。

 正文段落里"投标报价满分30分"这类**项级**上限与真实文档总分无法在文本层可靠
 区分, 直喂会把 30 变成总分基准并高置信报出假缺口(原型)。组声明对本身
 自带 fail-closed 门禁, 故只放开组声明这一侧。
 """
    capture = capture_scoring_declarations(
        [(1, "| 分组 | 评分因素 | 分值 |")],
        [],
        supplementary_pages=[(3, "投标报价满分30分。本项目评分总分100分")],
    )

    assert capture.total_candidates == ()


def test_declaration_pages_from_signals_offers_pairwise_joins():
    """同一句声明被拆进 declarations 与 headings 且页内次序已丢失。

    项目2476 实测: 同页还夹着"品牌子目满分值附表"等无关条目, 整页拼成一段会把
    "分别占30%、"与"15%、55%的权重"隔断。故须给出两两拼接候选, 让正确组合可被解析。
    """
    signals = {
        "declarations": [
            {"page": 6, "text": "评分细则按技术标、资信标、商务标分别编写,满分均为100,"},
            {"page": 6, "text": "(6)品牌子目满分值附表:"},
            {"page": 1, "text": "2.2.1 分值构成:见评标办法前附表。"},
        ],
        "headings": [
            {"page": 6, "text": "按技术标、资信标、商务标分别占30%、15%、55%的权重"},
        ],
    }

    pages = declaration_pages_from_signals(signals)
    page6 = [text for page, text in pages if page == 6]

    assert any(
        "满分均为100" in text and "分别占30%" in text and "品牌子目" not in text
        for text in page6
    ), "必须存在一个不被无关条目隔断的拼接候选"


def test_supplementary_pages_reject_noisy_flat_pairs():
    """项目2475 实测反例: 正文拼接后表头词粘进标签, 该伪组不得进入组声明。

    页文本拼出的"…条款号 条款内容 编列内容承包人建议书:17分"会被平铺规则捕成
    标签"编列内容承包人建议书", 提取侧永不匹配, 表现为凭空 -17 分组缺口。补充页
    只走并列卷权重声明这一条强门禁通路, 正是为了挡掉这类噪声。
    """
    capture = capture_scoring_declarations(
        [(1, "| 分组 | 评分因素 | 分值 |")],
        [],
        supplementary_pages=[(1, "条款号 条款内容 编列内容承包人建议书:17分分值构成")],
    )

    assert capture.pairs == ()


def test_markdown_anchor_and_flat_prelude_dedupe_to_three_clean_groups() -> None:
    """: 同一构成的Markdown锚点+flat prelude只能产出三组，禁止`| 价格`副本。"""
    markdown = (
        "| 价格评分（总分：15分） |  |  |\n"
        "| 分值构成：价格评分：15分；技术评分：53分；商务评分：32分 |\n"
        "| 技术评分（总分：53分） |  |  |\n"
        "| 商务评分（总分：32分） |  |  |"
    )

    capture = capture_scoring_declarations([(1, markdown)], [])

    assert [(pair.label, pair.norm_label, pair.score) for pair in capture.pairs] == [
        ("价格评分", "价格", 15.0),
        ("技术评分", "技术", 53.0),
        ("商务评分", "商务", 32.0),
    ]


def test_flat_composition_strips_structural_column_header_from_first_label() -> None:
    """: 表头`编列内容`粘到首组时只剥结构噪声，保留真实`资信业绩部分`。"""
    flattened = (
        "条款号 条款内容 编列内容资信业绩部分:3分"
        "分值构成 项目管理大纲部分:30分 "
        "2.2.1(总分100分) 投标报价:60分 其他评分因素:7分"
    )

    capture = capture_scoring_declarations([(3, flattened)], [])

    assert [(pair.label, pair.score) for pair in capture.pairs] == [
        ("资信业绩部分", 3.0),
        ("项目管理大纲部分", 30.0),
        ("投标报价", 60.0),
        ("其他评分因素", 7.0),
    ]


def test_declaration_pages_from_signals_tolerates_missing_snapshot():
    """存量文档无信号快照时返回空列表(不得抛错破坏确认页主链)。"""
    assert declaration_pages_from_signals(None) == []
    assert declaration_pages_from_signals({}) == []
    assert declaration_pages_from_signals({"declarations": "bad"}) == []


def test_section_number_before_composition_heading_is_not_a_score():
    """章节号不是分值: "响应性评审标准:" 后紧跟标题 "2.2 分值构成与评分标准"。

    实测(holdout-24 第1页)旧实现把它捕成"响应性评审标准=2.2分"——这里的"分"是
    "分值构成"的首字, 不是分值单位。该伪声明会污染组对合计(302.2/102.2), 使真实
    的三卷折算声明(各100分、30%/15%/55%)对不上百分制而整体降级为无基准。
    """
    capture = _capture_text("2.1.3 响应性评审标准:", "2.2 分值构成与评分标准")

    assert [p.norm_label for p in capture.pairs] == []


def test_weighted_share_declaration_captured_with_raw_max_and_weight():
    """并列卷权重声明进入账本: 原始满分与权重分列, 不折算进 score。"""
    capture = _capture_text(
        "(2)评分细则按技术标、资信标、商务标分别编写,满分均为100,",
        "按技术标、资信标、商务标分别占30%、15%、55%的权重计算最终评标得分",
    )

    shares = [
        (p.norm_label, p.score, p.weight)
        for p in capture.pairs
        if p.source == "weighted_share"
    ]
    assert shares == [("技术", 100.0, 0.3), ("资信", 100.0, 0.15), ("商务", 100.0, 0.55)]


def test_multi_volume_weights_paired_locally_bug760():
    """: 同段两卷各自权重必须就近配对, 禁止首个权重广播给全部卷定义。

 doc1923 实测形态——整张 scoring_table chunk 作为一段传入时, 原实现用
 `search` 取整段第一个权重(40%)套给技术与价格两卷, 折算和由 100 变成 80,
 L1 基准随之失配、文档被误降级。判据是"标签就近", 与卷名词面无关。
 """
    capture = _capture_text(
        "技术总分100分，按40%权重进行折算",
        "价格总分100分，按60%权重进行折算",
    )

    weights = {p.norm_label: p.weight for p in capture.pairs if p.source == "weighted_def"}
    assert weights == {"技术": 0.4, "价格": 0.6}


def test_weighted_def_ignores_weight_declared_before_label():
    """权重必须落在其所属卷标签之后; 前置的无归属权重不得被任意卷认领。"""
    capture = _capture_text("按40%权重进行折算 技术总分100分")

    assert [p for p in capture.pairs if p.source == "weighted_def"] == []


def test_colonless_composition_listing_three_pairs():
    """无冒号构成列举: "分值构成: 商务20分；技术30分；价格50分" → 三对捕获。"""
    capture = _capture_text("分值构成: 商务20分；技术30分；价格50分")
    assert {(p.norm_label, p.score) for p in capture.pairs} == {
        ("商务", 20.0), ("技术", 30.0), ("价格", 50.0),
    }


def test_total_sentence_into_candidates():
    """声明总分句: "总分100分" → total_candidates 含 100, 不产生组对。"""
    capture = _capture_text("本项目评标总分100分。")
    assert 100.0 in capture.total_candidates
    assert capture.pairs == ()


def test_paren_group_line_captured():
    """括注式组声明: "资信标（100分）"整行捕获为 group_paren 对。"""
    capture = _capture_text("资信标（100分）")
    assert [(p.norm_label, p.score, p.source) for p in capture.pairs] == [("资信", 100.0, "group_paren")]


def test_bare_numbered_group_line_captured():
    """组尾缀裸数字式: "一、价格部分 20"编号标题行捕获为 group_bare 对。"""
    capture = _capture_text("一、价格部分 20")
    assert [(p.norm_label, p.score, p.source) for p in capture.pairs] == [("价格", 20.0, "group_bare")]


def test_table_trigger_row_numeric_cell_total_candidate():
    """表内触发行: 纯数字格("100")作总分候选, 不误产组对。"""
    capture = capture_scoring_declarations([], [(3, [["分值构成", "100"]])])
    assert capture.total_candidates == (100.0,)
    assert capture.pairs == ()


def test_table_weight_cell_attaches_to_paren_pair():
    """同行权重格: "资信标（100分）"同行存在"10%"格时括注对附加权重0.1并带页码。"""
    capture = capture_scoring_declarations([], [(2, [["资信标（100分）", "10%"]])])
    assert [(p.norm_label, p.score, p.weight, p.page) for p in capture.pairs] == [
        ("资信", 100.0, 0.1, 2)
    ]


def test_group_column_captures_bare_group_names_gated_out_by_wordface():
    """: 分组列结构命中时, 无词面后缀的"设计方案/施工方案"必须入组对。

 这两个标签不以 _GROUP_SUFFIX_GATE 任一后缀结尾, 词面路径恒排除(suffix_gate),
 是 doc1933 仅捕 3/5 组的直接原因。
 """
    grid = [
        ["条款号", "评审因素", "评审标准"],
        ["1", "设计方案 （26分）", "认识和措施 （26分）"],
        ["2", "施工方案 （30分）", "施工方案与技术措施 （30分）"],
        ["3", "投标报价评分标准 （44分）", "投标报价得分 （44分）"],
    ]
    capture = capture_scoring_declarations([(2, "")], [(2, grid)])

    assert {(p.label, p.score, p.source) for p in capture.pairs} == {
        ("设计方案", 26.0, "group_column"),
        ("施工方案", 30.0, "group_column"),
        ("投标报价评分标准", 44.0, "group_column"),
    }
    assert "group_column_tables=1" in capture.diagnostics


def test_group_column_suppresses_item_level_full_score_as_doc_total():
    """: 结构化表内的"满分30分"是项级规则文本, 不得升格为文档级总分候选。

 doc1933 正是因此把 30 当基准, 与真实合计 100 比出 gap=70 的高置信假缺口。
 """
    grid = [
        ["评审因素", "评审标准", "评分标准"],
        ["甲组 （70分）", "子项一 （70分）", "完善得70分"],
        ["投标报价评分标准 （30分）", "投标报价得分 （30分）", "投标报价满分30分。以评标基准价为基数"],
    ]
    page_text = "投标报价满分30分。以评标基准价为基数"
    capture = capture_scoring_declarations([(2, page_text)], [(2, grid)])

    assert 30.0 not in capture.total_candidates, "项级满分不得作文档总分基准"
    assert round(sum(p.score for p in capture.pairs), 4) == 100.0


def test_out_of_table_total_sentence_still_kept_when_value_differs():
    """作用域排除须精准: 表外真实总分声明(值不同于表内项级满分)不得被连带丢弃。"""
    grid = [
        ["评审因素", "评审标准", "评分标准"],
        ["甲组 （70分）", "子项一 （70分）", "完善得70分"],
        ["投标报价评分标准 （30分）", "投标报价得分 （30分）", "投标报价满分30分"],
    ]
    capture = capture_scoring_declarations([(2, "本项目评审总分100分")], [(2, grid)])

    assert 100.0 in capture.total_candidates
    assert 30.0 not in capture.total_candidates


def test_non_structural_table_falls_back_to_wordface_path_unchanged():
    """零回归: 不满足结构判据的表(无明细列可闭合)仍走既有词面路径与门控诊断。"""
    grid = [
        ["评审项目", "评审标准"],
        ["质量保证措施（15分）", "措施完善得15分"],
        ["安全保证措施（10分）", "措施完善得10分"],
    ]
    capture = capture_scoring_declarations([(1, "")], [(1, grid)])

    assert "group_column_tables=0" in capture.diagnostics
    assert all(p.source != "group_column" for p in capture.pairs)


def test_anchor_gated_pairs_from_closed_groups():
    """组锚点门控版: 组满分合计闭合到100时以 anchor_gated 入账。"""
    capture = _capture_text("商务部分满分：34分", "技术部分满分：66分")
    assert {(p.norm_label, p.score, p.source) for p in capture.pairs} == {
        ("商务", 34.0, "anchor_gated"),
        ("技术", 66.0, "anchor_gated"),
    }


class TestLedgerFlatPairsConnectorLabels:
    """ 账本专用平铺配对: 连接符标签保全 + 超长汉字链拒绝。

 护栏对象为 `_ledger_flat_pairs`——它取代了共享的 `_vp._FLAT_PAIR_PATTERN`。
 T0 语料对本能力零覆盖（58 份样本无连接符标签），故行为只能由本组断言锁定。
 """

    @staticmethod
    def _labels(text: str) -> list[str]:
        return [p["label"] for p in _ledger_flat_pairs(text)]

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("分值构成: 其他因素-技术能力：15分", "其他因素-技术能力"),
            ("分值构成: 其他因素—商务能力：12分", "其他因素—商务能力"),
            ("分值构成: 服务方案_A类响应：8分", "服务方案_A类响应"),
            ("分值构成: 其他因素-技术能力-实施保障：10分", "其他因素-技术能力-实施保障"),
        ],
    )
    def test_connector_label_kept_whole(self, text: str, expected: str):
        """连接符标签必须整体保全。

        旧共享正则只认连续汉字，会把 `其他因素-技术能力` 截成 `技术能力`、
        把 `服务方案_A类响应` 截成 `类响应`——前缀丢失后与声明侧键空间对不上，
        reconciliation 报出虚假缺口。
        """
        assert self._labels(text) == [expected]

    def test_connector_tail_without_cjk_rejected(self):
        """连接符后仅字母数字不足以确认是业务标签，须有汉字收尾。"""
        assert self._labels("分值构成: 其他因素-A1：10分") == []

    @pytest.mark.parametrize(
        "text",
        [
            "分值构成: 技术评审与商务评审综合评定后的价格部分：30分",
            "分值构成 以下按技术评审与商务评审综合评定后确定的价格部分：30分",
        ],
    )
    def test_long_cjk_run_rejected_instead_of_truncated(self, text: str):
        """超长汉字链无合法左边界起点时整体不出对，而非截出无意义短标签。

        旧正则的 {2,12} 会从链尾倒数 12 字硬切，产出 `评审综合评定后的价格部分`
        这类既非组名也非项名的垃圾标签，直接污染组对合计。
        """
        assert self._labels(text) == []

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("分值构成: 商务部分：20分", ["商务部分"]),
            ("分值构成: 商务部分：20分，技术部分：30分，价格部分：50分",
             ["商务部分", "技术部分", "价格部分"]),
            ("分值构成\n商务部分：20分", ["商务部分"]),  # 行首标签靠哨兵空格过左边界断言
            ("分值构成: 总分：100分", []),               # 跳过词维持排除
        ],
    )
    def test_plain_label_behavior_unchanged(self, text: str, expected: list[str]):
        """零回归: 常规标签/多标签/行首标签/跳过词的结论与改动前逐字一致。"""
        assert self._labels(text) == expected


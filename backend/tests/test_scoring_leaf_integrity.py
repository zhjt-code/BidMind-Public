""" 叶子完整性强证据（分值多重集语义）防御测试。

覆盖: assess_leaf_integrity v2——结构统计选分值列、序号列剔除、名称无关的
 分值多重集严格相等、闭合 100±0.01、回退括注路径、门禁短路。
不做: 不调 LLM、不访问 DB。

语义变更记录(用户裁决 v2 数字优先绑定):
 - v1 按 (名称,分值) 签名严格相等, 真实文档 LLM 项名与网格原文不等导致零召回;
 - v2 数字批准+名称否决: 分值多重集相等且闭合 100 才通过; fail-closed 叶子名
 清单相对提取项名有缺失(双向包含口径)即否决, 拦同分值幻觉替换。
 - 已声明天花板: 名称清单提不出名字的行不产生否决(同分互换在该窗口不可见)。
"""
from __future__ import annotations

import pytest

from app.core.extractor.scoring.leaf_integrity import assess_leaf_integrity
from app.core.extractor.scoring.ledger.contracts import (
    BasisLevel,
    LeafIntegrityEvidence,
    LedgerItemEntry,
    LedgerReport,
    LedgerStatus,
)

pytestmark = pytest.mark.scoring


def _make_report(**overrides) -> LedgerReport:
    base: dict = dict(
        status=LedgerStatus.REVIEW_REQUIRED,
        basis_level=BasisLevel.L3_DOC_TOTAL,
        basis_low_confidence=False,
        expected_total=100.0,
        extracted_total=100.0,
        total_gap=0.0,
    )
    base.update(overrides)
    return LedgerReport(**base)


def _entries(*scores: float) -> tuple[LedgerItemEntry, ...]:
    """按分值批量造条目; 名称刻意与网格原文不同(v2 名称无关)。"""
    return tuple(
        LedgerItemEntry(name=f"提取项{i}", score=s, volume_label="")
        for i, s in enumerate(scores)
    )


# ── 真实表形态网格(3477 缩影): 表头行 + 序号列 + 分值列 + 规则列含"得X-Y分"区间 ──
_REAL_SHAPE_GRID = [
    ["序号", "评审因素", "分值", "评审标准", "评审依据"],
    ["1", "技术方案与技术措施", "5.00",
     "合理可行、针对性强的得 4-5分； 一般的得 2-3分； 差的得 0-1分。", "技术路线说明"],
    ["2", "工期保证措施", "2.00", "切实可行并具有针对性的加2分； 不完善的加1分。", ""],
    ["3", "质量保障措施", "3.00", "切实可行得3分； 一般的得2分； 较低得 0-1分。", ""],
    ["4", "QHSE保障措施", "3.00", "得当的得3 分； 较为得当的得2分； 不全面得0-1分。", ""],
    ["5", "资源配置情况", "15.00", "完全可以保证工程进度得15分； 基本满足得10分； 勉强满足得5分。", "提供清单或照片"],
    ["6", "施工人员配备情况", "15.00", "80人及以上得15分； 70至79人得10分； 61至69人得5分。", ""],
]
# 商务表: 含 0 分扣分行(不入正分多重集)
_COMM_GRID = [
    ["序号", "评审因素", "分值", "评审标准", "评审依据"],
    ["1", "投标文件内容与格式", "1.00", "该项满分1分，每项扣 0.2 分，扣完为止。", ""],
    ["2", "业绩", "3.00", "每项可得[1]分。最高可得[3 分]。", "需同时提供合同、发票"],
    ["3", "失信行为扣", "0.00", "1.失信分以开标当日中", ""],
]
# 价格表: 单数据行, 无法结构选列, 走括注/清单回退路径
_PRICE_GRID = [
    ["分组", "评分因素", "评分内容与标准", "分值"],
    ["价格部分", "", "", ""],
    ["", "报价评分", "评审价=有效报价;得分=(评标基准价÷有效报价)×53。", "53"],
]


# ─────────────────────────── 正向 verified=True 路径 ──────────────────────────

class TestAssessLeafIntegrityVerified:
    def test_real_shape_grid_with_rule_ranges_verified(self) -> None:
        """ 根因1回归: 规则列含"得X-Y分"区间不得使叶子行被弃。

 v1 把规则列区间当分值信号 → 歧义弃行 → 召回 4/9; v2 结构选中分值列后
 规则列不参与竞争, 三网格并集多重集 {5,2,3,3,15,15}+{1,3}+{53}=100。
 条目名用 3477 实测落库名(名称否决闸要求覆盖可提取的叶子名)。
 """
        names_scores = (
            ("技术方案与技术措施", 5.0), ("工期保证措施", 2.0), ("质量保障措施", 3.0),
            ("QHSE保障措施", 3.0), ("资源配置情况", 15.0), ("施工人员配备情况", 15.0),
            ("投标文件内容与格式", 1.0), ("业绩", 3.0), ("报价", 53.0),
        )
        entries = tuple(
            LedgerItemEntry(name=n, score=s, volume_label="") for n, s in names_scores
        )

        evidence = assess_leaf_integrity(
            [_REAL_SHAPE_GRID, _COMM_GRID, _PRICE_GRID], entries, _make_report()
        )

        assert isinstance(evidence, LeafIntegrityEvidence)
        assert evidence.verified is True
        assert evidence.basis == "single_100"
        assert evidence.inventory_count == 9
        assert evidence.matched_count == 9
        assert evidence.total == pytest.approx(100.0)

    def test_name_variation_between_grid_and_entry_tolerated(self) -> None:
        """ 根因1回归: 网格"报价评分"对提取"报价"——名称差异不参与判定。"""
        entries = (
            LedgerItemEntry(name="报价", score=53.0, volume_label="报价"),
            LedgerItemEntry(name="技术综合", score=47.0, volume_label="技术"),
        )
        grid = [
            ["序号", "评审因素", "分值"],
            ["一", "报价评分", "53"],
            ["二", "技术综合评分", "47"],
        ]

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is True

    def test_duplicate_scores_on_both_sides_verified(self) -> None:
        """重复分值是合法形态(3477 实测 3,3 与 15,15): 两侧多重集相等即通过。"""
        entries = tuple(
            LedgerItemEntry(name=n, score=s, volume_label="")
            for n, s in (("指标一", 30.0), ("指标二", 30.0), ("指标三", 20.0), ("指标四", 20.0))
        )
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "指标一", "30"],
            ["乙", "指标二", "30"],
            ["丙", "指标三", "20"],
            ["丁", "指标四", "20"],
        ]

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is True
        assert evidence.inventory_count == 4

    def test_embedded_paren_grid_fallback_verified(self) -> None:
        """无独立分值列的括注形态网格走 leaf_inventory 回退路径。"""
        grid = [
            ["1.1", "施工方案（60分）", "方案完整合理"],
            ["1.2", "企业业绩（40分）", "每项10分"],
        ]
        entries = (
            LedgerItemEntry(name="施工方案", score=60.0, volume_label=""),
            LedgerItemEntry(name="企业业绩", score=40.0, volume_label=""),
        )

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is True

    def test_leaf_total_within_score_match_tolerance_verified(self) -> None:
        """浮点合计与 100 的差值不超过 0.01 时仍可通过。"""
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "叶子甲", "33.333"],
            ["乙", "叶子乙", "33.333"],
            ["丙", "叶子丙", "33.34"],
        ]
        entries = _entries(33.333, 33.333, 33.34)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.total == pytest.approx(100.006)
        assert evidence.verified is True


# ─────────────────────────── 结构选列的判定细节 ─────────────────────────────

class TestScoreColumnSelection:
    def test_sequence_column_excluded_from_score_candidates(self) -> None:
        """序号列(1,2,3... 严格递增且首值≤2)按算术判据剔除, 不得当分值列。

        本网格若误选序号列, 多重集为 {1,2} 合计 3 ≠ 100 → 假否; 正确选分值列
        后 {60,40}=100 → verified。
        """
        grid = [
            ["序号", "评审因素", "分值"],
            ["1", "叶子壹", "60"],
            ["2", "叶子贰", "40"],
        ]
        entries = _entries(60.0, 40.0)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is True
        assert evidence.total == pytest.approx(100.0)

    def test_zero_score_rows_excluded_from_multiset(self) -> None:
        """0 分行(扣分说明项)不入正分多重集; 提取侧 0 分项同样不入。"""
        entries = (
            *_entries(60.0, 40.0),
            LedgerItemEntry(name="失信行为扣", score=0.0, volume_label=""),
        )
        grid = [
            ["序号", "评审因素", "分值"],
            ["一", "叶子壹", "60"],
            ["二", "叶子贰", "40"],
            ["三", "失信扣分说明", "0.00"],
        ]

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is True
        assert evidence.inventory_count == 2

    def test_total_row_not_polluting_multiset(self) -> None:
        """合计行(总分100)不得进入多重集, 否则合计翻倍必然假否。"""
        grid = [
            ["序号", "评审因素", "分值"],
            ["一", "叶子壹", "60"],
            ["二", "叶子贰", "40"],
            ["", "合计", "100"],
        ]
        entries = _entries(60.0, 40.0)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is True
        assert evidence.inventory_count == 2

    def test_ambiguous_two_numeric_columns_fails_closed(self) -> None:
        """两个非序号数值列并存 → 选列歧义, fail-closed 不得随意采信任一列。"""
        grid = [
            ["评审因素", "分值", "权重分"],
            ["叶子壹", "60", "55"],
            ["叶子贰", "40", "45"],
        ]
        entries = _entries(60.0, 40.0)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is False


# ─────────────────────────── 否决路径（verified=False）─────────────────────────

class TestAssessLeafIntegrityNotVerified:
    def test_score_mismatch_offsets_cancel_to_100_not_verified(self) -> None:
        """网格 20/30/50, 提取 25/25/50: 错位抵消合计仍 100 → 多重集不等, 拒绝。"""
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "评审甲", "20"],
            ["乙", "评审乙", "30"],
            ["丙", "评审丙", "50"],
        ]
        entries = _entries(25.0, 25.0, 50.0)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is False

    def test_parent_leak_breaks_closure_not_verified(self) -> None:
        """父组行分值(43)与其叶子同时入册 → 合计 143 ≠ 100, 闭合闸拒绝。

 防线在多重集语义下由闭合闸承担: 父子同现必然破坏 100。
 """
        grid = [
            ["序号", "评审因素", "分值"],
            ["一", "技术评审组", "43"],
            ["甲", "技术方案", "23"],
            ["乙", "技术措施", "20"],
            ["丙", "商务综合", "57"],
        ]
        entries = _entries(43.0, 23.0, 20.0, 57.0)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is False

    def test_missing_item_not_verified(self) -> None:
        """网格 3 叶提取只有 2 项: 多重集不等 → 拒绝(即使提取侧无幻觉)。"""
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "叶子甲", "50"],
            ["乙", "叶子乙", "30"],
            ["丙", "叶子丙", "20"],
        ]
        entries = _entries(50.0, 30.0)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is False

    def test_hallucinated_extra_item_not_verified(self) -> None:
        """提取比网格多一项(幻觉补分) → 多重集不等, 拒绝。"""
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "叶子甲", "60"],
            ["乙", "叶子乙", "30"],
        ]
        entries = _entries(60.0, 30.0, 10.0)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is False

    def test_empty_grids_not_verified(self) -> None:
        """无网格输入 → not verified, inventory_count=0。"""
        evidence = assess_leaf_integrity([], _entries(60.0, 40.0), _make_report())

        assert evidence.verified is False
        assert evidence.inventory_count == 0

    def test_single_empty_grid_not_verified(self) -> None:
        """网格存在但为空列表 → not verified。"""
        evidence = assess_leaf_integrity([[]], _entries(60.0, 40.0), _make_report())

        assert evidence.verified is False

    @pytest.mark.parametrize("second", [20.0, 60.0])
    def test_leaf_total_80_or_120_not_verified(self, second: float) -> None:
        """网格多重集合计 80/120 ≠ 100 → 拒绝(即使两侧多重集相等)。"""
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "叶子壹", "60"],
            ["乙", "叶子贰", str(second)],
        ]
        entries = _entries(60.0, second)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is False

    @pytest.mark.parametrize("second", [39.0, 41.0])
    def test_leaf_total_one_point_from_100_not_verified(self, second: float) -> None:
        """合计 99/101 不得借用账本 1 分容差通过(严格 SCORE_MATCH_TOLERANCE)。"""
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "叶子甲", "60"],
            ["乙", "叶子乙", str(second)],
        ]
        entries = _entries(60.0, second)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.total == pytest.approx(60.0 + second)
        assert evidence.verified is False

    def test_insufficiency_flags_short_circuit_not_verified(self) -> None:
        """报告带充分性拒收(生产链组头自证/叶子覆盖缺口落点) → 直接拒绝。

        同分值幻觉替换在 assess 层不可见(名称无关), 生产链由 bridge 的
        叶子覆盖缺口注入 insufficiency_flags, 经此闸拦截。
        """
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "叶子壹", "60"],
            ["乙", "叶子贰", "40"],
        ]
        entries = _entries(60.0, 40.0)

        evidence = assess_leaf_integrity(
            [grid], entries,
            _make_report(insufficiency_flags=("叶子覆盖缺口: 叶子壹",)),
        )

        assert evidence.verified is False

    def test_same_score_hallucination_vetoed_by_name_gate(self) -> None:
        """名称否决闸: 网格叶子名"技术答辩方案"未被任何提取项名覆盖 → 拒绝。

        多重集 {70,30} 两侧相等且闭合 100, 若无名称否决闸即假绿(同分幻觉替换);
        多 scope 下 bridge 覆盖闸不广播, 本闸是唯一名称防线。
        """
        grid = [
            ["序号", "评审因素", "分值"],
            ["一", "技术答辩方案", "30"],
            ["二", "报价评分方案", "70"],
        ]
        entries = (
            LedgerItemEntry(name="报价评分方案", score=70.0, volume_label=""),
            LedgerItemEntry(name="综合评分方案", score=30.0, volume_label=""),  # 幻觉
        )

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is False

    def test_multi_scheme_signal_short_circuit_not_verified(self) -> None:
        """报告带多方案信号 → 直接拒绝。"""
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "叶子壹", "60"],
            ["乙", "叶子贰", "40"],
        ]
        entries = _entries(60.0, 40.0)

        evidence = assess_leaf_integrity(
            [grid], entries, _make_report(multi_scheme_signal="疑似多套方案")
        )

        assert evidence.verified is False


# ─────────────────────────── 固定 100 比较（不依赖 report.expected_total）──────

class TestAssessLeafIntegrityFixedTotal:
    _GRID = [
        ["序号", "评审因素", "分值"],
        ["甲", "叶子壹", "60"],
        ["乙", "叶子贰", "40"],
    ]

    def test_expected_total_none_leaves_100_still_verified(self) -> None:
        """expected_total=None 时, 多重集闭合 100 仍应 verified(固定比较 100)。"""
        evidence = assess_leaf_integrity(
            [self._GRID], _entries(60.0, 40.0),
            _make_report(expected_total=None, total_gap=None),
        )
        assert evidence.verified is True

    def test_expected_total_wrong_value_leaves_100_still_verified(self) -> None:
        """expected_total=80(错误值)但多重集严格闭合 100 → 仍应 verified。"""
        evidence = assess_leaf_integrity(
            [self._GRID], _entries(60.0, 40.0),
            _make_report(expected_total=80.0, total_gap=20.0),
        )
        assert evidence.verified is True


# ─────────────────────────── matched_count 为多重集交集计数 ───────────────────

class TestMatchedCountAccuracy:
    def test_partial_mismatch_matched_count_is_intersection(self) -> None:
        """网格 {50,30,20} vs 提取 {50,30,25}: matched=交集 2, 且不 verified。"""
        grid = [
            ["序号", "评审因素", "分值"],
            ["甲", "叶子甲", "50"],
            ["乙", "叶子乙", "30"],
            ["丙", "叶子丙", "20"],
        ]
        entries = _entries(50.0, 30.0, 25.0)

        evidence = assess_leaf_integrity([grid], entries, _make_report())

        assert evidence.verified is False
        assert evidence.matched_count == 2


# ─────────────────────────── 声明网格不进入 integrity 路径 ────────────────────

class TestDeclarationGridsExcluded:
    def test_declaration_grids_do_not_produce_duplicate_leaf_evidence(self) -> None:
        """单 scope 时 declaration_grids 只供 capture, 不得混入 integrity 评估。"""
        from app.core.extractor.scoring.ledger.scoped_reconciliation import (
            ScopedLedgerRequest,
            coordinate_scoped_ledger,
        )

        grid = [["1.1", "叶子壹", "60"], ["1.2", "叶子贰", "40"]]
        decl_grid = [["1.1", "叶子壹", "60"], ["1.2", "叶子贰", "40"]]

        entries = (
            LedgerItemEntry(name="叶子壹", score=60.0),
            LedgerItemEntry(name="叶子贰", score=40.0),
        )
        ewrs = tuple((e, "") for e in entries)

        request = ScopedLedgerRequest(
            pages=((1, "合计100分"),),
            chunk_scopes=((None, None),),
            grids=((1, grid),),
            grid_scopes=((None, None),),
            entries_with_rules=ewrs,
            declaration_grids=((2, decl_grid),),
        )
        report = coordinate_scoped_ledger(request)
        assert report.leaf_integrity.verified is True

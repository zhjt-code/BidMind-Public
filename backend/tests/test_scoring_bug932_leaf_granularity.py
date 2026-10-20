""" 评分叶子粒度门禁防御测试。

覆盖: 结构性续行缝合(D)、网格叶子清单三形态分值信号、组头行自证检测(A)、
 账本叶子覆盖 review reason(B)、锚定回退源(E)、采纳覆盖决胜(C)。
夹具为项目 3427 文档 2498 scoring chunk 191216 网格逐字形态
（父组行冒充单项直判 green 的实证形态）。
不做: 不调 LLM、不访问 DB、不改生产段 parser。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.extractor.scoring.leaf_inventory import (
    covered_leaf_count,
    group_header_echo_names,
    is_group_header_name,
    leaf_inventory_from_chunks,
    leaf_inventory_from_grid,
    missing_leaf_names,
    stitch_grid_continuation_rows,
)
from app.core.extractor.scoring.ledger.contracts import LedgerStatus
from app.core.extractor.scoring.ledger.declaration_capture import (
    capture_scoring_declarations,
    declaration_grids_from_signals,
)
from app.core.extractor.scoring.ledger.extractor_bridge import (
    observe_scoring_ledger_shadow,
)
from app.core.extractor.scoring.ledger.ledger_reconciliation import (
    reconcile_scoring_ledger,
)
from app.core.extractor.scoring.scoring_green_verdict import resolve_scoring_verdict
from app.core.extractor.scoring.vlm.candidate_gates import filter_anchored_items

pytestmark = pytest.mark.scoring

# 项目 3427 scoring chunk 网格（跨页折行、组头带括注分值、三形态分值信号并存）
GRID_3427 = [
    ["条款号", "评分因素", "", "评分标准"],
    ["2.2.4 （1）", "技术标评分标准 （35分）", "内容完整性和编制水平", "内容完整、编排合理有序0-5分"],
    ["", "", "施工方案与技术措施", "针对性、先进性、节约工程成本0-5分"],
    ["", "", "质量管理体系与措施", "保证招标文件规定的质量、措施切实可行0-5分"],
    ["", "", "安全管理体系与措施", "保证工程施工文明、安全、措施切实可行0-5分"],
    ["", "", "环境保护、文明施工管理体系与", "保证工程环境、措施切合实际0-5分"],
    ["", "", "技术措施", ""],
    ["", "", "工程进度计划与确保工期的技术措施", "工程进度安排合理、切合实际0-5分"],
    ["", "", "资源配备计划", "材料配备满足工程要求、人员配备合理、施工机械满足工程要求0-3分"],
    ["", "", "总平面图", "现场措施齐全、布置合理、可行0-2分"],
    ["", "", "注：以上如有缺项，则该项得0分。", ""],
    ["2.2.4 (2)", "商务标评分标准 （50分）",
     "投标总报价与评标基准价相比，相等者得满分50分，每低于评标基准价1%在满分基础上扣0.5分", ""],
    ["2.2.4 (3)", "综合标评分标准 （15分）", "企业业绩 （5分）",
     "自2019年1月1日以来做过类似业绩的，每有一份得 2.5分，最多得5分"],
    ["", "", "优惠承诺 （5分）",
     "优惠承诺应是书面的符合工程实际情况，确保依法依规，优惠合理，详实可行。"],
    ["", "", "", "优惠承诺符合工程实际情况，确保依法依规，优惠基本合理，基本详实可行。"],
    ["", "", "履职尽责承诺 （3分）",
     "具有全面、详实、可行、合法有效的书面保证技术措施落实到位的承诺和落实不到位的处理承诺"],
    ["", "", "招标人意见（2 分）",
     "招标人根据投标人投标文件编制的详实、合理、符合性及响应程度等综合印象进行打分。"],
    ["", "", "注：以上承诺如有缺项，则该项得0分。", ""],
]

# 3427 分值构成声明表行（signals v2 逐字形态, 修复后 L1 高置信）
DECL_CELLS_3427 = [
    "2.2.1",
    "分值构成 （总分100分）",
    "",
    "技术标(施工组织设计)：  35  分商务标：  50  分综合标：  15  分",
]
# 声明所在页文本(总分100分句为文档级总分候选来源, 与真实 chunk 正文一致)
DECL_PAGE_TEXT_3427 = "2.2.1 分值构成 （总分100分） 评标办法前附表"

_TECH_LEAVES = [
    ("内容完整性和编制水平", 5.0),
    ("施工方案与技术措施", 5.0),
    ("质量管理体系与措施", 5.0),
    ("安全管理体系与措施", 5.0),
    ("环境保护、文明施工管理体系与技术措施", 5.0),
    ("工程进度计划与确保工期的技术措施", 5.0),
    ("资源配备计划", 3.0),
    ("总平面图", 2.0),
]
_COMPOSITE_LEAVES = [
    ("企业业绩", 5.0),
    ("优惠承诺", 5.0),
    ("履职尽责承诺", 3.0),
    ("招标人意见", 2.0),
]


def _fake_item(name: str, score: float, volume_label: str) -> SimpleNamespace:
    return SimpleNamespace(
        score_item_name=name, score_weight=score, volume_label=volume_label,
        scheme=None, lot=None, score_rules=["规则"], deduction_rules=[],
    )


# 生产段假完整产物: 父组行冒充单项 + 真叶子混排, 合计恰 100
_FAKE_COMPLETE_ITEMS = [
    _fake_item("技术标评分标准", 35.0, "技术标评分标准"),
    _fake_item(
        "投标总报价与评标基准价相比，相等者得满分50分，每低于评标基准价1%在满分",
        50.0, "商务标评分标准",
    ),
    _fake_item("企业业绩", 5.0, "综合标评分标准"),
    _fake_item("优惠承诺", 5.0, "综合标评分标准"),
    _fake_item("履职尽责承诺", 3.0, "综合标评分标准"),
    _fake_item("招标人意见", 2.0, "综合标评分标准"),
]


def _chunk_3427(page_num: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        page_num=page_num,
        content=DECL_PAGE_TEXT_3427,
        chunk_metadata={"scoring_payload": {"version": 1, "grid": GRID_3427}},
    )


class TestStitching:
    """D: 结构性续行缝合——结构判据, 不依赖连接符词面。"""

    def test_cross_page_orphan_stitched_into_previous_row(self):
        stitched = stitch_grid_continuation_rows(GRID_3427)
        names = [row[2] for row in stitched if len(row) > 2]
        assert "环境保护、文明施工管理体系与技术措施" in names
        assert "技术措施" not in names  # 孤行已被吸收

    def test_annotation_rows_never_stitched(self):
        stitched = stitch_grid_continuation_rows(GRID_3427)
        annotations = [
            row for row in stitched
            if any(str(c).strip().startswith("注：") for c in row)
        ]
        assert len(annotations) == 2  # 两条注释行原样保留, 不并入叶子

    def test_scored_short_name_row_not_stitched(self):
        """总平面图行自带 0-2 分区间信号, 绝不能被误判为续行。"""
        stitched = stitch_grid_continuation_rows(GRID_3427)
        names = [row[2] for row in stitched if len(row) > 2]
        assert "总平面图" in names

    def test_iterative_multi_row_continuation(self):
        grid = [
            ["1", "环境保护、文明施工管理", "5"],
            ["", "体系与技术", ""],
            ["", "措施", ""],
        ]
        stitched = stitch_grid_continuation_rows(grid)
        assert len(stitched) == 1
        assert stitched[0][1] == "环境保护、文明施工管理体系与技术措施"


class TestInventory3427:
    """网格叶子清单: 三形态分值信号 + fail-closed 名判定。"""

    def test_all_twelve_leaves_extracted(self):
        inventory = leaf_inventory_from_grid(GRID_3427)
        got = {(name, score) for name, score in inventory}
        for expected in [*_TECH_LEAVES, *_COMPOSITE_LEAVES]:
            assert expected in got, f"缺失叶子: {expected}"

    def test_group_header_rows_not_in_inventory(self):
        inventory = leaf_inventory_from_grid(GRID_3427)
        names = [name for name, _ in inventory]
        assert all("评分标准" not in name for name in names)

    def test_commercial_group_fail_closed(self):
        """商务组分值藏在规则长句里, 提不出叶子信号 → 不断言(宁漏不误报)。"""
        inventory = leaf_inventory_from_grid(GRID_3427)
        assert all(score != 50.0 for _, score in inventory)

    def test_leaf_scores_sum_matches_declared_groups(self):
        inventory = leaf_inventory_from_grid(GRID_3427)
        assert sum(score for _, score in inventory) == 50.0  # 技术35 + 综合15

    def test_no_grid_yields_empty(self):
        assert leaf_inventory_from_chunks([SimpleNamespace(chunk_metadata={})]) == ()
        assert leaf_inventory_from_grid([]) == ()

    def test_missing_names_against_fake_complete_items(self):
        chunks = [_chunk_3427()]
        inventory = leaf_inventory_from_chunks(chunks)
        extracted = [i.score_item_name for i in _FAKE_COMPLETE_ITEMS]
        missing = missing_leaf_names(inventory, extracted)
        assert set(missing) == {name for name, _ in _TECH_LEAVES}

    def test_covered_leaf_count(self):
        inventory_names = [name for name, _ in _TECH_LEAVES]
        assert covered_leaf_count(inventory_names, ["总平面图", "资源配备计划"]) == 2
        assert covered_leaf_count(inventory_names, ["技术标评分标准"]) == 0


class TestGroupHeaderEcho:
    """A: 组头行自证检测——组语义标准形态或声明同键组名, 不得作为叶子。"""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("技术标评分标准", True),
            ("技术标评分标准 （35分）", True),
            ("商务标评审标准", True),
            ("投标总报价", False),
            ("商务标响应情况", False),
            ("技术标评分标准：施工组织设计", False),  # 中缀非结尾, 是具体项
        ],
    )
    def test_is_group_header_name(self, name: str, expected: bool):
        assert is_group_header_name(name) is expected

    def test_declared_key_echo_with_group_suffix(self):
        echo = group_header_echo_names(
            [("技术标", 35.0), ("投标报价", 30.0), ("商务项", 20.0)],
            {"技术", "投标报价", "商务"},
        )
        assert echo == ["技术标"]  # 同键+组尾缀; 投标报价无组尾缀是真叶子

    def test_zero_score_entry_not_flagged(self):
        assert group_header_echo_names([("技术标评分标准", 0.0)], {"技术"}) == []

    def test_exempt_keys_release_leafless_group_row(self):
        """: 网格提不出叶子的组, 组头形态计分行是唯一真实计分行, 豁免。"""
        entries = [("商务标评分标准", 50.0), ("技术标评分标准", 35.0)]
        keys = {"商务", "技术", "综合"}
        # 无豁免: 两条都拦(旧行为)
        assert group_header_echo_names(entries, keys) == [
            "商务标评分标准", "技术标评分标准",
        ]
        # 豁免商务(无叶子组): 商务放行, 技术(有清单叶子)仍拦
        assert group_header_echo_names(
            entries, keys, exempt_norm_keys={"商务"}
        ) == ["技术标评分标准"]


class TestLedgerAxis:
    """A+B 账本轴: 3427 假完整形态必须 REVIEW_REQUIRED, 真 13 叶保持可通过。"""

    def _capture_3427(self):
        grids = declaration_grids_from_signals(
            {"declaration_table_rows": [{"page": 2, "cells": DECL_CELLS_3427}]}
        )
        return capture_scoring_declarations([(2, DECL_PAGE_TEXT_3427)], grids)

    def _entries(self, items):
        from app.core.extractor.scoring.ledger.contracts import LedgerItemEntry

        return [
            LedgerItemEntry(
                name=i.score_item_name, score=i.score_weight,
                volume_label=i.volume_label,
            )
            for i in items
        ]

    def test_fake_complete_flagged_by_group_header_echo(self):
        """核心回归: 父组行自证组轴闭合, 修复前 RECONCILED(假绿), 修复后必须报警。"""
        report = reconcile_scoring_ledger(
            self._capture_3427(), self._entries(_FAKE_COMPLETE_ITEMS)
        )
        assert report.status is LedgerStatus.REVIEW_REQUIRED
        assert any("组头行自证" in d for d in report.diagnostics)
        assert resolve_scoring_verdict(None, report) != "green"

    def test_true_leaves_still_reconcile(self):
        true_items = [
            *[_fake_item(n, s, "技术标评分标准") for n, s in _TECH_LEAVES],
            _fake_item("投标总报价", 50.0, "商务标评分标准"),
            *[_fake_item(n, s, "综合标评分标准") for n, s in _COMPOSITE_LEAVES],
        ]
        report = reconcile_scoring_ledger(
            self._capture_3427(), self._entries(true_items)
        )
        assert report.status is LedgerStatus.RECONCILED
        assert not any("组头行自证" in d for d in report.diagnostics)

    def test_low_confidence_soft_basis_cannot_bypass_leaf_gate(self):
        """3427 实测直绿路径: L1 低置信 + gap=0 命中 软基准豁免,
 叶子粒度违规必须走结构化通道顶穿豁免, 禁止 verdict 转绿。"""
        grids = declaration_grids_from_signals(
            {"declaration_table_rows": [{"page": 2, "cells": DECL_CELLS_3427}]}
        )
        # 页文本无"总分100分"句 → 无显式总分候选佐证 → L1 恒低置信(真实形态)
        capture = capture_scoring_declarations([(2, "")], grids)
        report = reconcile_scoring_ledger(
            capture, self._entries(_FAKE_COMPLETE_ITEMS)
        )
        assert report.status is LedgerStatus.REVIEW_REQUIRED
        assert report.basis_low_confidence is True
        assert any("组头行自证" in flag for flag in report.insufficiency_flags)
        assert resolve_scoring_verdict(None, report) != "green"

    def test_extra_review_reasons_force_review(self):
        true_items = [
            *[_fake_item(n, s, "技术标评分标准") for n, s in _TECH_LEAVES],
            _fake_item("投标总报价", 50.0, "商务标评分标准"),
            *[_fake_item(n, s, "综合标评分标准") for n, s in _COMPOSITE_LEAVES],
        ]
        report = reconcile_scoring_ledger(
            self._capture_3427(), self._entries(true_items),
            extra_review_reasons=("叶子未逐项展开(网格有分值行未提取): 某项",),
        )
        assert report.status is LedgerStatus.REVIEW_REQUIRED


class TestBridgeCoverage:
    """B 集成: 触发裁决/确认页同源入口必须点名缺失叶子。"""

    def test_fake_complete_items_yield_missing_leaf_reason(self):
        report = observe_scoring_ledger_shadow(
            [_chunk_3427()],
            list(_FAKE_COMPLETE_ITEMS),
            document_id=2498,
            declaration_grids=declaration_grids_from_signals(
                {"declaration_table_rows": [{"page": 2, "cells": DECL_CELLS_3427}]}
            ),
            log_summary=False,
        )
        assert report is not None
        assert report.status is LedgerStatus.REVIEW_REQUIRED
        joined = " ".join(report.diagnostics)
        assert "叶子未逐项展开" in joined
        assert "施工方案与技术措施" in joined
        assert "环境保护、文明施工管理体系与技术措施" in joined
        assert resolve_scoring_verdict(None, report) != "green"

    def test_full_leaves_no_missing_reason(self):
        true_items = [
            *[_fake_item(n, s, "技术标评分标准") for n, s in _TECH_LEAVES],
            _fake_item("投标总报价", 50.0, "商务标评分标准"),
            *[_fake_item(n, s, "综合标评分标准") for n, s in _COMPOSITE_LEAVES],
        ]
        report = observe_scoring_ledger_shadow(
            [_chunk_3427()], true_items, document_id=2498,
            declaration_grids=declaration_grids_from_signals(
                {"declaration_table_rows": [{"page": 2, "cells": DECL_CELLS_3427}]}
            ),
            log_summary=False,
        )
        assert report is not None
        assert not any("叶子未逐项展开" in d for d in report.diagnostics)
        assert report.status is LedgerStatus.RECONCILED

    def test_real_thirteen_items_turn_green(self):
        """ 核心回归: 3427 实测 13 项形态(商务组行以组名命名)必须转绿。

 商务组网格提不出叶子(分值藏规则长句), 「商务标评分标准 50」是该组唯一
 真实计分行, 不得被组头自证误伤; 技术组头冒充形态仍由既有测试锁拦截。
 """
        thirteen = [
            *[_fake_item(n, s, "技术标评分标准\n（35分）") for n, s in _TECH_LEAVES],
            _fake_item("商务标评分标准", 50.0, "商务标"),
            *[_fake_item(n, s, "综合标评分标准\n（15分）") for n, s in _COMPOSITE_LEAVES],
        ]
        report = observe_scoring_ledger_shadow(
            [_chunk_3427()], thirteen, document_id=2498,
            declaration_grids=declaration_grids_from_signals(
                {"declaration_table_rows": [{"page": 2, "cells": DECL_CELLS_3427}]}
            ),
            log_summary=False,
        )
        assert report is not None
        assert not any("组头行自证" in f for f in report.insufficiency_flags)
        assert not any("叶子未逐项展开" in d for d in report.diagnostics)
        assert report.status is LedgerStatus.RECONCILED
        assert resolve_scoring_verdict(None, report) == "green"

    def test_multi_scope_skips_leaf_assertion(self):
        items = [
            _fake_item("甲项", 50.0, ""), _fake_item("乙项", 50.0, ""),
        ]
        items[0].lot = "lot_A"
        items[1].lot = "lot_B"
        report = observe_scoring_ledger_shadow(
            [_chunk_3427()], items, document_id=2498, log_summary=False,
        )
        assert report is not None
        assert not any("叶子未逐项展开" in d for d in report.diagnostics)


class TestAnchorFallback:
    """E: 名称轴回退到缝合网格名, 分值轴不放宽。"""

    _FULL_NAME = "环境保护、文明施工管理体系与技术措施"

    def _anchor_with_page_break(self) -> str:
        from app.core.extractor.scoring.vlm.candidate_gates import _compact

        return _compact(
            "环境保护、文明施工管理体系与 保证工程环境、措施切合实际0-5分"
            " 第2页 共5页 评分因素 评分标准 技术措施"
        )

    def _item(self, score: float) -> SimpleNamespace:
        return SimpleNamespace(score_item_name=self._FULL_NAME, score_weight=score)

    def test_cross_page_name_rejected_without_fallback(self):
        kept, dropped = filter_anchored_items(
            [self._item(5.0)], self._anchor_with_page_break()
        )
        assert kept == []
        assert len(dropped) == 1

    def test_cross_page_name_passes_with_inventory_fallback(self):
        kept, dropped = filter_anchored_items(
            [self._item(5.0)], self._anchor_with_page_break(),
            fallback_names=(self._FULL_NAME,),
        )
        assert len(kept) == 1
        assert dropped == []

    def test_fabricated_score_still_rejected_with_fallback(self):
        kept, dropped = filter_anchored_items(
            [self._item(40.0)], self._anchor_with_page_break(),
            fallback_names=(self._FULL_NAME,),
        )
        assert kept == []
        assert "40" in dropped[0]

    def test_short_fallback_name_not_matched(self):
        item = SimpleNamespace(score_item_name="预算书", score_weight=5.0)
        kept, _ = filter_anchored_items(
            [item], self._anchor_with_page_break(), fallback_names=("预算书",),
        )
        assert kept == []  # compact 后 <4 字不走回退


class TestGroupRuleHints:
    """: 无清单叶子的组把计分行原文附进重询反馈。"""

    def test_unresolved_group_rows_only_commercial(self):
        from app.core.extractor.scoring.leaf_inventory import (
            unresolved_group_rule_rows,
        )

        hints = unresolved_group_rule_rows(GRID_3427)
        assert len(hints) == 1
        group_cell, rule_cell = hints[0]
        assert "商务标评分标准" in group_cell
        assert "投标总报价" in rule_cell

    def test_hint_suppressed_when_kept_covers_rule(self):
        from app.core.extractor.scoring.vlm.rescue_missing_items import (
            group_rule_row_hints,
        )

        chunks = [_chunk_3427()]
        uncovered = group_rule_row_hints(
            chunks, [SimpleNamespace(score_item_name="企业业绩")]
        )
        assert any("投标总报价" in h for h in uncovered)
        covered = group_rule_row_hints(
            chunks, [SimpleNamespace(score_item_name="投标总报价")]
        )
        assert covered == []

    def test_retry_feedback_names_commercial_rule_row(self):
        from app.core.extractor.scoring.vlm.rescue_rounds import (
            _build_retry_feedback,
        )

        feedback = _build_retry_feedback(
            SimpleNamespace(feedback="核计43/100未闭合"),
            [SimpleNamespace(score_item_name="企业业绩")],
            [],
            [_chunk_3427()],
        )
        assert "投标总报价" in feedback
        assert "必须按原文输出" in feedback


class TestAdoptionCoverage:
    """C: 守恒打平时叶子覆盖多者胜, 等分不再是终局地板。"""

    def test_equal_gap_candidate_with_more_coverage_adopted(self):
        from app.core.extractor.scoring.scoring_rescue_integration import (
            ScoringRescueIntegrationResult,
        )
        from app.core.extractor.scoring.vlm.rescue_rounds import (
            RescueRoundsRequest,
            _resolve_adoption,
        )

        current_items = [
            SimpleNamespace(score_item_name="技术标评分标准", score_weight=35.0,
                            scheme=None, lot=None),
            SimpleNamespace(score_item_name="其他既有项", score_weight=55.0,
                            scheme=None, lot=None),
        ]
        tail = ScoringRescueIntegrationResult(
            items=current_items,
            report=SimpleNamespace(needs_review=True, total=90.0,
                                   expected_total=100.0, note=""),
            fidelity_sidecar={}, diagnostics=[], l3_diagnostics=[],
            adopted=False, mode="shadow",
        )
        kept = [
            SimpleNamespace(score_item_name="施工方案与技术措施", score_weight=45.0,
                            scheme=None, lot=None, source_pages=[2]),
            SimpleNamespace(score_item_name="资源配备计划", score_weight=45.0,
                            scheme=None, lot=None, source_pages=[2]),
        ]
        outcome = SimpleNamespace(
            validation=SimpleNamespace(
                kind="partial",
                to_score_totals_report=lambda: SimpleNamespace(
                    needs_review=True, total=90.0, expected_total=100.0, note="",
                ),
            ),
        )
        request = RescueRoundsRequest(
            tail=tail, db=object(), document_id=1, scoring_chunks=[],
            volume_defs={}, anchor_text="x", max_rounds=1, meta={},
        )
        meta = request.meta
        result = _resolve_adoption(
            request,
            ((0, 0, 2, -1, -10.0), kept, outcome, "amber"),
            compat_expected=100.0,
            leaf_names=("施工方案与技术措施", "资源配备计划", "总平面图"),
        )
        assert result is not None
        assert result.adopted is True
        assert meta["status"] == "ok_needs_review"
        assert meta.get("leaf_coverage_edge") is True

    def test_false_green_adoption_rejected_on_coverage_regression(self):
        """: 假绿直采必须有叶子覆盖下限——候选覆盖低于现状即拒。"""
        from app.core.extractor.scoring.scoring_rescue_integration import (
            ScoringRescueIntegrationResult,
        )
        from app.core.extractor.scoring.vlm.rescue_rounds import (
            RescueRoundsRequest,
            _resolve_adoption,
        )

        current_items = [
            SimpleNamespace(score_item_name="施工方案与技术措施", score_weight=50.0,
                            scheme=None, lot=None),
            SimpleNamespace(score_item_name="资源配备计划", score_weight=50.0,
                            scheme=None, lot=None),
        ]
        tail = ScoringRescueIntegrationResult(
            items=current_items,
            report=SimpleNamespace(needs_review=False, total=100.0,
                                   expected_total=100.0, note=""),
            fidelity_sidecar={}, diagnostics=[], l3_diagnostics=[],
            adopted=False, mode="shadow",
        )
        kept = [
            SimpleNamespace(score_item_name="施工方案与技术措施", score_weight=43.0,
                            scheme=None, lot=None, source_pages=[2]),
        ]
        outcome = SimpleNamespace(
            validation=SimpleNamespace(kind="partial",
                                       to_score_totals_report=lambda: None),
        )
        request = RescueRoundsRequest(
            tail=tail, db=object(), document_id=1, scoring_chunks=[],
            volume_defs={}, anchor_text="x", max_rounds=1, meta={},
            false_green_current=True,
        )
        result = _resolve_adoption(
            request,
            ((0, 0, 1, -1, -57.0), kept, outcome, "amber"),
            compat_expected=100.0,
            leaf_names=("施工方案与技术措施", "资源配备计划"),
        )
        assert result is None
        assert request.meta["reject_reason"] == "leaf_coverage_regression"

    def test_false_green_adoption_without_inventory_keeps_bug897(self):
        """无清单证据时维持 原契约: 假绿现状不作拒收基准, 直采。"""
        from app.core.extractor.scoring.scoring_rescue_integration import (
            ScoringRescueIntegrationResult,
        )
        from app.core.extractor.scoring.vlm.rescue_rounds import (
            RescueRoundsRequest,
            _resolve_adoption,
        )

        tail = ScoringRescueIntegrationResult(
            items=[SimpleNamespace(score_item_name="旧项", score_weight=100.0,
                                   scheme=None, lot=None)],
            report=SimpleNamespace(needs_review=False, total=100.0,
                                   expected_total=100.0, note=""),
            fidelity_sidecar={}, diagnostics=[], l3_diagnostics=[],
            adopted=False, mode="shadow",
        )
        kept = [
            SimpleNamespace(score_item_name="真实叶子项", score_weight=43.0,
                            scheme=None, lot=None, source_pages=[2]),
        ]
        outcome = SimpleNamespace(
            validation=SimpleNamespace(
                kind="partial",
                to_score_totals_report=lambda: SimpleNamespace(
                    needs_review=True, total=43.0, expected_total=100.0, note="",
                ),
            ),
        )
        request = RescueRoundsRequest(
            tail=tail, db=object(), document_id=1, scoring_chunks=[],
            volume_defs={}, anchor_text="x", max_rounds=1, meta={},
            false_green_current=True,
        )
        result = _resolve_adoption(
            request,
            ((0, 0, 0, -1, -57.0), kept, outcome, "amber"),
            compat_expected=100.0,
            leaf_names=(),
        )
        assert result is not None
        assert result.adopted is True

    def test_equal_gap_and_equal_coverage_still_rejected(self):
        from app.core.extractor.scoring.scoring_rescue_integration import (
            ScoringRescueIntegrationResult,
        )
        from app.core.extractor.scoring.vlm.rescue_rounds import (
            RescueRoundsRequest,
            _resolve_adoption,
        )

        current_items = [
            SimpleNamespace(score_item_name="施工方案与技术措施", score_weight=90.0,
                            scheme=None, lot=None),
        ]
        tail = ScoringRescueIntegrationResult(
            items=current_items,
            report=SimpleNamespace(needs_review=True, total=90.0,
                                   expected_total=100.0, note=""),
            fidelity_sidecar={}, diagnostics=[], l3_diagnostics=[],
            adopted=False, mode="shadow",
        )
        kept = [
            SimpleNamespace(score_item_name="施工方案与技术措施", score_weight=90.0,
                            scheme=None, lot=None, source_pages=[2]),
        ]
        outcome = SimpleNamespace(
            validation=SimpleNamespace(kind="partial",
                                       to_score_totals_report=lambda: None),
        )
        request = RescueRoundsRequest(
            tail=tail, db=object(), document_id=1, scoring_chunks=[],
            volume_defs={}, anchor_text="x", max_rounds=1, meta={},
        )
        result = _resolve_adoption(
            request,
            ((0, 0, 1, -1, -10.0), kept, outcome, "amber"),
            compat_expected=100.0,
            leaf_names=("施工方案与技术措施",),
        )
        assert result is None
        assert request.meta["status"] == "rejected"

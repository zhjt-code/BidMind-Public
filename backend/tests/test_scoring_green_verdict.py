"""评分三态判绿权威源防御测试（verdict 单源）。

覆盖: 与前端 ScoringTotalsBanner 三态公式的逐分支对齐——
 doc2478 假绿形态 / 折算假差额豁免 / 软基准自洽豁免 /
 组构成让位(-776) / 多包 scope 黄蓝态 / 双缺失 None /
 提取项适配(penalty 跳过与规则并集) / 触发包装兜底 amber。
不做: 不调 LLM、不访问 DB(触发包装用桩替身)。
"""
from __future__ import annotations

from inspect import getsource, signature
from types import SimpleNamespace
from typing import Any, Optional, get_type_hints

import pytest

from app.core.extractor.scoring.ledger.contracts import (
    BasisLevel,
    GroupReconciliation,
    LeafIntegrityEvidence,
    LedgerReport,
    LedgerStatus,
    ScopedReport,
)
from app.core.extractor.scoring.scoring_green_verdict import (
    VERDICT_AMBER,
    VERDICT_BLUE,
    VERDICT_GREEN,
    _leaf_integrity_strong,
    resolve_extraction_trigger_verdict,
    resolve_scoring_verdict,
    scoring_totals_entries_from_items,
)

pytestmark = pytest.mark.scoring


def _ledger(**overrides) -> LedgerReport:
    base = dict(
        status=LedgerStatus.REVIEW_REQUIRED,
        basis_level=BasisLevel.L3_DOC_TOTAL,
        basis_low_confidence=False,
        expected_total=100.0,
        extracted_total=100.0,
        total_gap=0.0,
    )
    base.update(overrides)
    return LedgerReport(**base)


def _green_payload(**overrides) -> dict:
    payload = {
        "needs_review": False,
        "note": "校验通过",
        "by_volume": {"technical": 100.0},
        "by_lot": {},
        "volume_checks": {},
        "weight_sum_ok": True,
        "item_count": 8,
        "missing_score_count": 0,
        "missing_rule_count": 0,
        "structure_review_required": False,
        "diagnostics": [],
    }
    payload.update(overrides)
    return payload


class TestVerdictCore:
    def test_both_missing_returns_none(self):
        assert resolve_scoring_verdict(None, None) is None

    def test_doc2478_false_green_with_ledger_review_required_is_amber(self):
        """ 实测形态: legacy 假绿(2项凑满100)+账本未归组100 → amber。"""
        ledger = _ledger(
            expected_total=20.0, extracted_total=100.0, total_gap=80.0,
            basis_low_confidence=True, unlabeled_sum=100.0,
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_AMBER

    def test_legacy_amber_without_ledger_is_amber(self):
        payload = _green_payload(needs_review=True)
        assert resolve_scoring_verdict(payload, None) == VERDICT_AMBER

    def test_both_green_is_green(self):
        ledger = _ledger(status=LedgerStatus.RECONCILED)
        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_GREEN

    def test_folding_artifact_gap_is_green(self):
        """: 折算制假差额(200原始-100折算)不构成非绿。"""
        payload = _green_payload(
            by_volume={"technical": 100.0, "price": 100.0},
            volume_checks={
                "technical": {"within": True, "weight": 0.5, "sum": 100.0, "expected": 100.0},
                "price": {"within": True, "weight": 0.5, "sum": 100.0, "expected": 100.0},
            },
        )
        ledger = _ledger(
            expected_total=100.0, extracted_total=200.0, total_gap=100.0,
        )
        assert resolve_scoring_verdict(payload, ledger) == VERDICT_GREEN

    def test_folding_artifact_with_real_leak_stays_amber(self):
        """差额偏离折算量(实际漏提)时豁免失效, 照常 amber。"""
        payload = _green_payload(
            by_volume={"technical": 100.0, "price": 90.0},
            volume_checks={
                "technical": {"within": True, "weight": 0.5, "sum": 100.0, "expected": 100.0},
                "price": {"within": True, "weight": 0.5, "sum": 90.0, "expected": 100.0},
            },
        )
        ledger = _ledger(
            expected_total=100.0, extracted_total=190.0, total_gap=90.0,
        )
        assert resolve_scoring_verdict(payload, ledger) == VERDICT_AMBER

    def test_soft_basis_self_consistent_is_green(self):
        """: 分值闭合+唯一原因是基准低置信 → 绿(仅信息缺口)。"""
        ledger = _ledger(
            basis_level=BasisLevel.L2_DERIVED_GROUP, basis_low_confidence=True,
            expected_total=100.0, extracted_total=100.0, total_gap=0.0,
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_GREEN

    def test_legacy_superseded_by_complete_group_shares_is_green(self):
        """: 账本闭合且组行完整覆盖时 legacy 复核让位。"""
        payload = _green_payload(needs_review=True, missing_score_count=1)
        ledger = _ledger(
            status=LedgerStatus.RECONCILED,
            group_rows=(
                GroupReconciliation(
                    label="设计方案", norm_label="设计方案",
                    declared=40.0, extracted_sum=40.0, gap=0.0,
                ),
                GroupReconciliation(
                    label="施工方案", norm_label="施工方案",
                    declared=60.0, extracted_sum=60.0, gap=0.0,
                ),
            ),
        )
        assert resolve_scoring_verdict(payload, ledger) == VERDICT_GREEN

    def test_scope_applicable_gap_is_amber(self):
        """: 多包模式下 applicable scope 真实缺口 → amber。"""
        ledger = _ledger(
            global_comparison_allowed=False,
            scope_reports=(
                ScopedReport(
                    scope=("scheme_1", "采购包1"), status=LedgerStatus.REVIEW_REQUIRED,
                    basis=BasisLevel.L3_DOC_TOTAL, expected_total=100.0,
                    extracted_total=90.0, total_gap=-10.0, applicability="applicable",
                ),
            ),
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_AMBER

    def test_scope_unresolved_without_gap_is_blue(self):
        """: 仅适用关系未确认且无真实缺口 → blue 中性。"""
        ledger = _ledger(
            global_comparison_allowed=False,
            scope_reports=(
                ScopedReport(
                    scope=("scheme_1", "采购包1"), status=LedgerStatus.REVIEW_REQUIRED,
                    basis=BasisLevel.L3_DOC_TOTAL, expected_total=None,
                    extracted_total=100.0, total_gap=None, applicability="unresolved",
                ),
            ),
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_BLUE

    def test_scope_soft_self_consistent_multi_lot_is_green(self):
        """ 项目3414形态: 各标段L2推导基准自洽闭合(gap=0/unlabeled=0/无不足)
 时不得蓝色阻断——与单包软基准豁免同构, 判绿。"""
        def _scope(lot: str) -> ScopedReport:
            return ScopedReport(
                scope=("__single__", lot), status=LedgerStatus.REVIEW_REQUIRED,
                basis=BasisLevel.L2_DERIVED_GROUP, expected_total=100.0,
                extracted_total=100.0, total_gap=0.0, applicability="unresolved",
                basis_low_confidence=True,
            )

        ledger = _ledger(
            global_comparison_allowed=False, basis_low_confidence=True,
            basis_level=BasisLevel.L2_DERIVED_GROUP,
            scope_reports=(_scope("标段三"), _scope("标段四")),
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_GREEN

    def test_scope_soft_exemption_requires_full_self_consistency(self):
        """软自洽豁免缺一不可: 存在未归组分值的 scope 仍蓝色阻断, 不得误判绿。"""
        clean = ScopedReport(
            scope=("__single__", "标段四"), status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L2_DERIVED_GROUP, expected_total=100.0,
            extracted_total=100.0, total_gap=0.0, applicability="unresolved",
            basis_low_confidence=True,
        )
        dirty = ScopedReport(
            scope=("__single__", "标段三"), status=LedgerStatus.REVIEW_REQUIRED,
            basis=BasisLevel.L2_DERIVED_GROUP, expected_total=100.0,
            extracted_total=100.0, total_gap=0.0, applicability="unresolved",
            basis_low_confidence=True, unlabeled_sum=30.0,
        )
        ledger = _ledger(
            global_comparison_allowed=False, basis_low_confidence=True,
            basis_level=BasisLevel.L2_DERIVED_GROUP,
            scope_reports=(clean, dirty),
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_BLUE

    def test_scope_misclassified_unresolved_with_gap_is_amber(self):
        """: 误降级 unresolved 但对账质量本应 applicable 且有缺口 → amber。"""
        ledger = _ledger(
            global_comparison_allowed=False,
            scope_reports=(
                ScopedReport(
                    scope=("scheme_1", "__single__"), status=LedgerStatus.REVIEW_REQUIRED,
                    basis=BasisLevel.L3_DOC_TOTAL, expected_total=100.0,
                    extracted_total=90.0, total_gap=-10.0, applicability="unresolved",
                ),
            ),
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_AMBER


class TestEntriesAdapter:
    def test_penalty_and_bonus_items_skipped(self):
        items = [
            SimpleNamespace(score_item_name="基础项", score_weight=60.0, fact_type="base_score",
                            score_rules=["规则"], deduction_rules=[], description="描述文本"),
            SimpleNamespace(score_item_name="扣分项", score_weight=0.0, fact_type="penalty",
                            score_rules=[], deduction_rules=["扣分"], description="描述文本"),
            SimpleNamespace(score_item_name="加分项", score_weight=0.0, fact_type="bonus",
                            score_rules=[], deduction_rules=[], description="描述文本"),
        ]
        entries = scoring_totals_entries_from_items(items)
        assert [e.diagnostic_name for e in entries] == ["基础项"]

    def test_has_rules_unions_score_and_deduction(self):
        items = [
            SimpleNamespace(score_item_name="只有扣分规则", score_weight=10.0,
                            fact_type="base_score", score_rules=[],
                            deduction_rules=["每缺一项扣2分"], description="描述文本"),
            SimpleNamespace(score_item_name="无规则", score_weight=10.0,
                            fact_type="base_score", score_rules=[], deduction_rules=[],
                            description="描述文本"),
        ]
        entries = scoring_totals_entries_from_items(items)
        assert entries[0].has_rules is True
        assert entries[1].has_rules is False


class TestExtractionTriggerWrapper:
    def test_zero_items_falls_back_to_amber(self, monkeypatch):
        """零提取(载荷 None)且账本不可得 → amber, 保证 VLM 安全网可达。"""
        from app.core.extractor.scoring.ledger import extractor_bridge

        monkeypatch.setattr(
            extractor_bridge, "observe_scoring_ledger_shadow",
            lambda *a, **k: None,
        )
        verdict = resolve_extraction_trigger_verdict(
            chunks=[SimpleNamespace(content="评分标准", page_num=1)],
            items=[], diagnostics=[], document_id=1, scoring_signals=None,
        )
        assert verdict == VERDICT_AMBER


# ── 步骤3A: 叶子完整性强证据最高优先级（RED → GREEN TDD）────────────────


def _noisy_group_ledger(leaf_integrity=None, **overrides) -> LedgerReport:
    """含组gap噪音的账本：组A声明50但提取53，gap=3，总分仍100。"""
    kw: dict = dict(
        status=LedgerStatus.REVIEW_REQUIRED,
        basis_level=BasisLevel.L1_EXPLICIT_GROUP,
        basis_low_confidence=False,
        expected_total=100.0,
        extracted_total=100.0,
        total_gap=0.0,
        group_rows=(
            GroupReconciliation(
                label="技术评分标准", norm_label="技术评分标准",
                declared=50.0, extracted_sum=53.0, gap=3.0,
            ),
        ),
    )
    if leaf_integrity is not None:
        kw["leaf_integrity"] = leaf_integrity
    kw.update(overrides)
    return LedgerReport(**kw)


class TestLeafIntegrityPriority:
    """ 步骤3A: 叶子完整性强证据最高优先级。

 verified=True（全部门禁通过）时豁免归卷/总分噪音 → green；
 任一门禁不通过时退回既有逻辑，不误判绿。
 """

    def test_leaf_integrity_strong_uses_typed_ledger_contract(self):
        """强证据谓词只接收精确 LedgerReport，不以 Any/getattr 旁路契约。"""
        params = list(signature(_leaf_integrity_strong).parameters)
        hints = get_type_hints(_leaf_integrity_strong)

        assert params == ["ledger", "totals_payload"]
        assert hints["ledger"] is LedgerReport
        assert hints["totals_payload"] == Optional[dict[str, Any]]
        assert "getattr(" not in getsource(_leaf_integrity_strong)

    def test_verified_single_scope_overrides_group_gap_and_legacy_is_green(self):
        """普通 volume gap 非结构缺失时，verified=True 仍可豁免并判绿。

        场景: legacy report needs_review=True（卷偏差）+ 组gap=3 → 当前amber；
        加载强证据后应判绿。
        """
        evidence = LeafIntegrityEvidence(
            verified=True, basis="single_100",
            inventory_count=5, matched_count=5, total=100.0,
        )
        ledger = _noisy_group_ledger(leaf_integrity=evidence)
        # legacy 同时含 needs_review=True 和 volume 偏差
        payload = _green_payload(
            needs_review=True,
            structure_review_required=False,
            volume_checks={
                "技术": {"within": False, "weight": 1.0, "sum": 53.0, "expected": 50.0},
            },
        )
        assert resolve_scoring_verdict(payload, ledger) == VERDICT_GREEN

    def test_verified_leaf_does_not_override_structure_review_required(self):
        """结构丢失是独立强门禁，叶子 verified 不得短路为 green。"""
        evidence = LeafIntegrityEvidence(
            verified=True, basis="single_100",
            inventory_count=5, matched_count=5, total=100.0,
        )
        ledger = _noisy_group_ledger(leaf_integrity=evidence)
        payload = _green_payload(
            needs_review=True,
            structure_review_required=True,
        )

        assert resolve_scoring_verdict(payload, ledger) == VERDICT_AMBER

    def test_multi_scope_structure_review_is_not_superseded_by_scope_mode(self):
        """多 scope 也不得以 scope 接管或 per_scope_100 叶证据吞掉结构复核。"""
        evidence = LeafIntegrityEvidence(
            verified=True, basis="per_scope_100",
            inventory_count=10, matched_count=10, total=200.0,
        )
        ledger = _ledger(
            global_comparison_allowed=False,
            leaf_integrity=evidence,
            scope_reports=(
                ScopedReport(
                    scope=("scheme_1", "采购包1"),
                    status=LedgerStatus.RECONCILED,
                    basis=BasisLevel.L3_DOC_TOTAL,
                    expected_total=100.0,
                    extracted_total=100.0,
                    total_gap=0.0,
                ),
                ScopedReport(
                    scope=("scheme_1", "采购包2"),
                    status=LedgerStatus.RECONCILED,
                    basis=BasisLevel.L3_DOC_TOTAL,
                    expected_total=100.0,
                    extracted_total=100.0,
                    total_gap=0.0,
                ),
            ),
        )

        assert resolve_scoring_verdict(
            _green_payload(needs_review=True, structure_review_required=True),
            ledger,
        ) == VERDICT_AMBER

    def test_verified_but_missing_score_count_nonzero_blocks_green(self):
        """missing_score_count>0 时叶子强证据不得豁免 → 非green。"""
        evidence = LeafIntegrityEvidence(
            verified=True, basis="single_100",
            inventory_count=5, matched_count=5, total=100.0,
        )
        ledger = _noisy_group_ledger(leaf_integrity=evidence)
        assert resolve_scoring_verdict(
            _green_payload(missing_score_count=1), ledger
        ) != VERDICT_GREEN

    def test_verified_but_missing_rule_count_nonzero_blocks_green(self):
        """missing_rule_count>0 时叶子强证据不得豁免 → 非green。"""
        evidence = LeafIntegrityEvidence(
            verified=True, basis="single_100",
            inventory_count=5, matched_count=5, total=100.0,
        )
        ledger = _noisy_group_ledger(leaf_integrity=evidence)
        assert resolve_scoring_verdict(
            _green_payload(missing_rule_count=1), ledger
        ) != VERDICT_GREEN

    def test_verified_but_insufficiency_flags_blocks_green(self):
        """账本 insufficiency_flags 非空时叶子强证据不得豁免（账本层拒收）。"""
        evidence = LeafIntegrityEvidence(
            verified=True, basis="single_100",
            inventory_count=5, matched_count=5, total=100.0,
        )
        ledger = _noisy_group_ledger(
            leaf_integrity=evidence,
            insufficiency_flags=("某类别证据不足",),
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) != VERDICT_GREEN

    def test_verified_but_multi_scheme_signal_blocks_green(self):
        """multi_scheme_signal 非空时叶子强证据不得豁免（多方案歧义）。"""
        evidence = LeafIntegrityEvidence(
            verified=True, basis="single_100",
            inventory_count=5, matched_count=5, total=100.0,
        )
        ledger = _noisy_group_ledger(
            leaf_integrity=evidence,
            multi_scheme_signal="方案甲 vs 方案乙",
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) != VERDICT_GREEN

    def test_multi_scope_per_scope_100_overrides_scope_gap_is_green(self):
        """multi-scope: basis=per_scope_100 强证据豁免scope gap → green。"""
        evidence = LeafIntegrityEvidence(
            verified=True, basis="per_scope_100",
            inventory_count=9, matched_count=9, total=200.0,
        )
        ledger = _ledger(
            global_comparison_allowed=False,
            leaf_integrity=evidence,
            scope_reports=(
                ScopedReport(
                    scope=("scheme_1", "采购包1"),
                    status=LedgerStatus.REVIEW_REQUIRED,
                    basis=BasisLevel.L3_DOC_TOTAL,
                    expected_total=100.0, extracted_total=95.0, total_gap=-5.0,
                    applicability="applicable",
                ),
            ),
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_GREEN

    def test_multi_scope_per_scope_100_overrides_scope_info_only_is_green(self):
        """各包独立100为最高优先强证据，可覆盖仅 scope_info_only 的蓝态。"""
        evidence = LeafIntegrityEvidence(
            verified=True, basis="per_scope_100",
            inventory_count=9, matched_count=9, total=200.0,
        )
        ledger = _ledger(
            global_comparison_allowed=False,
            leaf_integrity=evidence,
            scope_reports=(
                ScopedReport(
                    scope=("scheme_1", "采购包1"),
                    status=LedgerStatus.REVIEW_REQUIRED,
                    basis=BasisLevel.L4_NONE,
                    expected_total=None,
                    extracted_total=100.0,
                    total_gap=None,
                    applicability="unresolved",
                ),
            ),
        )

        assert resolve_scoring_verdict(_green_payload(), ledger) == VERDICT_GREEN

    def test_multi_scope_wrong_basis_single_100_does_not_override(self):
        """multi-scope: basis=single_100（错误口径）不得豁免scope gap → 非green。"""
        evidence = LeafIntegrityEvidence(
            verified=True, basis="single_100",  # 错误：多包应用 per_scope_100
            inventory_count=9, matched_count=9, total=200.0,
        )
        ledger = _ledger(
            global_comparison_allowed=False,
            leaf_integrity=evidence,
            scope_reports=(
                ScopedReport(
                    scope=("scheme_1", "采购包1"),
                    status=LedgerStatus.REVIEW_REQUIRED,
                    basis=BasisLevel.L3_DOC_TOTAL,
                    expected_total=100.0, extracted_total=95.0, total_gap=-5.0,
                    applicability="applicable",
                ),
            ),
        )
        assert resolve_scoring_verdict(_green_payload(), ledger) != VERDICT_GREEN

    def test_unverified_leaf_integrity_preserves_group_gap_amber(self):
        """verified=False 时既有逻辑不变，组gap仍触发非green。"""
        evidence = LeafIntegrityEvidence(
            verified=False, inventory_count=5, matched_count=4, total=100.0,
        )
        ledger = _noisy_group_ledger(leaf_integrity=evidence)
        assert resolve_scoring_verdict(_green_payload(), ledger) != VERDICT_GREEN

    def test_default_leaf_integrity_preserves_group_gap_amber(self):
        """无自定义 leaf_integrity（默认 verified=False）时，既有逻辑不变。"""
        ledger = _noisy_group_ledger()  # leaf_integrity 使用 dataclass 默认值
        assert resolve_scoring_verdict(_green_payload(), ledger) != VERDICT_GREEN

    def test_verified_single_scope_empty_basis_does_not_green(self):
        """single scope: basis=''（空字符串）不构成强证据 → 非green（复审）。

 global_comparison_allowed=True 时必须 basis=='single_100'，
 空字符串口径不明确，不得豁免声明层噪音。
 """
        evidence = LeafIntegrityEvidence(
            verified=True, basis="",  # 空 basis → 不满足 single_100 要求
            inventory_count=5, matched_count=5, total=100.0,
        )
        ledger = _noisy_group_ledger(leaf_integrity=evidence)
        assert resolve_scoring_verdict(_green_payload(), ledger) != VERDICT_GREEN

    def test_verified_single_scope_per_scope_100_basis_does_not_green(self):
        """single scope: basis='per_scope_100'（多包口径）不构成强证据 → 非green（复审）。

 global_comparison_allowed=True 时只接受 'single_100'，
 错误口径不得绕过声明层噪音阻断。
 """
        evidence = LeafIntegrityEvidence(
            verified=True, basis="per_scope_100",  # 错误：单scope应用了多包口径
            inventory_count=5, matched_count=5, total=100.0,
        )
        ledger = _noisy_group_ledger(leaf_integrity=evidence)
        assert resolve_scoring_verdict(_green_payload(), ledger) != VERDICT_GREEN

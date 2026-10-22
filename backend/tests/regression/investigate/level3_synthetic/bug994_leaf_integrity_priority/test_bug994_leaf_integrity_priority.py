""" Level 3 合成样本：叶子完整性强证据缺席导致组gap噪音否定正确100分结果（TDD RED）。

复刻场景：9 个唯一叶子，分组汇总 53/43/4，算术总分 100。
声明层人为引入"组A声明50但提取53"的 gap=3 噪音。
leaf_integrity.verified=True 时应豁免噪音取绿；未验证时应保持 amber。

模块顶层使用哨兵模式捕获 ImportError，确保 pytest 收集成功。
每个测试首先调用 _require_interfaces，以 pytest.fail 显式表达"接口尚未实现"。
"""
from __future__ import annotations

import pytest

# ── 已存在的接口（安全导入）────────────────────────────────────────────────────
from app.core.extractor.scoring.ledger.contracts import (
    BasisLevel,
    GroupReconciliation,
    LedgerReport,
    LedgerStatus,
)
from app.core.extractor.scoring.scoring_green_verdict import (
    VERDICT_GREEN,
    resolve_scoring_verdict,
)

pytestmark = pytest.mark.scoring

# ── 目标接口：LeafIntegrityEvidence 尚未实现，哨兵模式防止收集错误 ─────────────
_LeafIntegrityEvidence = None

try:
    from app.core.extractor.scoring.ledger.contracts import (  # type: ignore[attr-defined]
        LeafIntegrityEvidence as _LeafIntegrityEvidence,
    )
except ImportError:
    pass


def _require_interfaces() -> None:
    """断言目标接口已实现；否则 pytest.fail 显式标红（非收集错误）。"""
    if _LeafIntegrityEvidence is None:
        pytest.fail(
            " [RED] 功能缺失: LeafIntegrityEvidence 尚未添加到 "
            "app.core.extractor.scoring.ledger.contracts"
        )


# ── 9 个唯一叶子：分组汇总 53/43/4，总分 100 ─────────────────────────────────
# Group A（5叶，提取和=53）: 13+12+11+9+8 = 53
# Group B（3叶，提取和=43）: 20+15+8 = 43
# Group C（1叶，提取和=4）:  4

_GROUP_A_GAP = GroupReconciliation(
    label="组A评分标准",
    norm_label="组a评分标准",
    declared=50.0,      # 人为噪音：声明应为53，此处故意填50
    extracted_sum=53.0,
    gap=3.0,            # gap=3 正常情况下触发 REVIEW_REQUIRED
    declared_source="anchor_gated",
)
_GROUP_B_OK = GroupReconciliation(
    label="组B评分标准",
    norm_label="组b评分标准",
    declared=43.0,
    extracted_sum=43.0,
    gap=0.0,
    declared_source="anchor_gated",
)
_GROUP_C_OK = GroupReconciliation(
    label="叶子壬",
    norm_label="叶子壬",
    declared=4.0,
    extracted_sum=4.0,
    gap=0.0,
    declared_source="anchor_gated",
)


def _make_leaf_integrity(*, verified: bool) -> object:
    """构造 LeafIntegrityEvidence；_require_interfaces() 未通过时不应调用此函数。

    basis 使用生产真实值 'single_100'（assess_leaf_integrity 单scope通过时恒输出此值）。
    verified=False 时保留 basis=''（默认），对应生产中未通过验证的形态。
    """
    return _LeafIntegrityEvidence(  # type: ignore[misc]
        verified=verified,
        basis="single_100" if verified else "",
        inventory_count=9,
        matched_count=9,
        total=100.0,
    )


def _make_noisy_ledger(*, leaf_integrity: object | None) -> LedgerReport:
    """构造含组gap噪音的账本报告，并携带叶子完整性证据（新字段）。

    LedgerReport.leaf_integrity 尚未定义时将抛出 TypeError；
    调用方应在此处捕获并以 pytest.fail 标红，而非让异常传播为 ERROR。
    """
    kwargs = {} if leaf_integrity is None else {"leaf_integrity": leaf_integrity}
    try:
        return LedgerReport(
            status=LedgerStatus.REVIEW_REQUIRED,
            basis_level=BasisLevel.L1_EXPLICIT_GROUP,
            basis_low_confidence=False,
            expected_total=100.0,
            extracted_total=100.0,
            total_gap=0.0,
            group_rows=(_GROUP_A_GAP, _GROUP_B_OK, _GROUP_C_OK),
            **kwargs,
        )
    except TypeError as exc:
        pytest.fail(
            f" [RED] 功能缺失: LedgerReport.leaf_integrity 字段尚未定义 → {exc}"
        )


def _clean_totals_payload() -> dict:
    """legacy 满分校验通过的载荷（算术层 100，无 needs_review 标记）。"""
    return {
        "needs_review": False,
        "note": "合计100通过",
        "by_volume": {"组A": 53.0, "组B": 43.0, "叶子壬": 4.0},
        "by_lot": {},
        "volume_checks": {},
        "weight_sum_ok": True,
        "item_count": 9,
        "missing_score_count": 0,
        "missing_rule_count": 0,
        "diagnostics": [],
    }


class TestBug994LeafIntegrityPriority:
    def test_verified_leaf_integrity_overrides_group_gap_returns_green(self) -> None:
        """叶子完整性 verified=True 时，组gap噪音应被豁免 → verdict=green。

        9 叶一一吻合 + 合计100 的强证据优先于声明层 gap=3 噪音，修复后应取绿。
        """
        _require_interfaces()

        ledger = _make_noisy_ledger(leaf_integrity=_make_leaf_integrity(verified=True))
        verdict = resolve_scoring_verdict(_clean_totals_payload(), ledger)

        assert verdict == VERDICT_GREEN, (
            f"期望 green（叶子强证据豁免组gap），实际 {verdict!r}"
        )

    def test_unverified_leaf_integrity_preserves_amber_from_group_gap(self) -> None:
        """叶子完整性 verified=False 时，组gap噪音不豁免 → verdict 非 green。

 防止仅靠"合计100"误判绿色（同类保护）。
 """
        _require_interfaces()

        ledger = _make_noisy_ledger(leaf_integrity=_make_leaf_integrity(verified=False))
        verdict = resolve_scoring_verdict(_clean_totals_payload(), ledger)

        assert verdict != VERDICT_GREEN, (
            f"期望非 green（未验证叶子证据不可豁免组gap），实际 {verdict!r}"
        )

    def test_no_leaf_integrity_preserves_amber_from_group_gap(self) -> None:
        """无叶子完整性证据时，组gap噪音不豁免 → verdict 非 green（向后兼容）。"""
        _require_interfaces()

        ledger = _make_noisy_ledger(leaf_integrity=None)
        verdict = resolve_scoring_verdict(_clean_totals_payload(), ledger)

        assert verdict != VERDICT_GREEN, (
            f"期望非 green（无叶子证据时组gap仍然有效），实际 {verdict!r}"
        )

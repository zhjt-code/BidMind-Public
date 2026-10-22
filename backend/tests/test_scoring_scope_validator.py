"""Task-1 RED: 统一 L2 裁判 — scope×lot 维度 failed_scopes 与确定性 expected_by_scope。

职责: 验证 validate_scoring_fact_set 暴露失败 scheme×lot 及只读确定性期望。
不做: 不测试 LLM、生产接线、数据库或 API 路由。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_facts import (  # noqa: E402
    ScoringFactSet,
    build_scoring_fact_set,
)
from app.core.extractor.scoring.scoring_scope_validator import (  # noqa: E402
    ScopeIdentity,
    ScoringScopeExpectation,
    ScoringScopeValidation,
    ScoringValidationKind,
    validate_scoring_fact_set,
)
from app.schemas.requirement import ExtractedScoringItem  # noqa: E402

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _item(
    name: str,
    score: float,
    *,
    lot: str | None = None,
    scheme: str | None = None,
    volume: str = "technical",
    fact_type: str = "base_score",
    volume_weight: float | None = None,
    volume_max: float | None = None,
) -> ExtractedScoringItem:
    return ExtractedScoringItem(
        category="TECH",
        score_item_name=name,
        description=f"{name}评分标准",
        score_weight=score,
        lot=lot,
        scheme=scheme,
        volume=volume,
        fact_type=fact_type,
        volume_weight=volume_weight,
        volume_max=volume_max,
        score_rules=[],
        deduction_rules=[],
        source_pages=[],
    )


# ---------------------------------------------------------------------------
# 1. EVIDENCE_GAP → failed_scopes 是 scheme×lot 有序元组
# ---------------------------------------------------------------------------

class TestFailedScopesFormat:
    """RED-1: failed_scopes 必须是 tuple[tuple[str,str],...]，维度 scheme×lot，排序稳定。"""

    def test_evidence_gap_failed_scopes_is_tuple_of_scope_tuples(self):
        """EVIDENCE_GAP 的 failed_scopes 必须是 tuple[tuple[str,str], ...]。"""
        items = [_item(f"a{i}", 10.0, lot="lot_a") for i in range(8)]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="t1"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP
        assert isinstance(val.failed_scopes, tuple), (
            f"failed_scopes 类型应为 tuple，实际: {type(val.failed_scopes)}"
        )
        assert len(val.failed_scopes) > 0, "失败 scope 应非空"
        for scope in val.failed_scopes:
            assert isinstance(scope, tuple) and len(scope) == 2, (
                f"每个 scope 应为 (scheme, lot) 二元组，实际: {scope}"
            )

    def test_evidence_gap_failed_scopes_dimension_is_scheme_lot(self):
        """维度必须是 scheme×lot，不能退化为纯 lot。"""
        items = (
            [_item(f"a{i}", 10.0, lot="lot_a") for i in range(6)]
            + [_item(f"b{i}", 10.0, lot="lot_b") for i in range(4)]
        )
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="t2"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP
        for scheme, lot in val.failed_scopes:
            assert isinstance(scheme, str) and len(scheme) > 0, f"scheme 不能为空: {scheme!r}"
            assert isinstance(lot, str) and len(lot) > 0, f"lot 不能为空: {lot!r}"

    def test_evidence_gap_failed_scopes_sorted_stable(self):
        """多 scope 的 failed_scopes 排序必须稳定（两次调用结果相同）。"""
        items = (
            [_item(f"a{i}", 10.0, lot="lot_a") for i in range(6)]
            + [_item(f"b{i}", 10.0, lot="lot_b") for i in range(4)]
        )
        fs = build_scoring_fact_set(items, source_id="t3")
        val1 = validate_scoring_fact_set(fs)
        val2 = validate_scoring_fact_set(fs)
        assert val1.failed_scopes == val2.failed_scopes, "failed_scopes 排序应稳定"
        assert val1.failed_scopes == tuple(sorted(val1.failed_scopes)), (
            f"failed_scopes 应按字典序排序，实际: {val1.failed_scopes}"
        )


# ---------------------------------------------------------------------------
# 2. COMPLETE/SCOPE_CONFLICT/TRANSFORMATION_LOSS → failed_scopes 为空元组
# ---------------------------------------------------------------------------

class TestNonGapFailedScopesEmpty:
    """非 EVIDENCE_GAP 分类的 failed_scopes 必须为空元组。"""

    def test_complete_has_empty_failed_scopes(self):
        """COMPLETE → failed_scopes == ()。"""
        items = [_item(f"x{i}", 10.0) for i in range(10)]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="c1"))
        assert val.kind == ScoringValidationKind.COMPLETE
        assert val.failed_scopes == (), (
            f"COMPLETE 的 failed_scopes 应为 ()，实际: {val.failed_scopes}"
        )

    def test_scope_conflict_has_empty_failed_scopes(self):
        """SCOPE_CONFLICT（显式 UNASSIGNED_LOT）→ kind==SCOPE_CONFLICT 且 failed_scopes==()。"""
        from app.core.extractor.scoring.scoring_facts import (
            ScoringFact,
            ScoringFactSet,
            ScoringScopeKey,
        )
        unassigned_fact = ScoringFact(
            fact_key="ua_fact",
            scope=ScoringScopeKey(
                lot=ScoringScopeKey.UNASSIGNED_LOT,
                scheme=ScoringScopeKey.SINGLE_SCHEME,
                volume="technical",
            ),
            score_item_name="未归属项",
            description="未归属采购包评分项",
            score_weight=50.0,
        )
        fs = ScoringFactSet(
            source_id="conflict_direct",
            observed_facts=[unassigned_fact],
            effective_fact_keys=["ua_fact"],
        )
        val = validate_scoring_fact_set(fs)
        assert val.kind == ScoringValidationKind.SCOPE_CONFLICT, (
            f"应为 SCOPE_CONFLICT，实际: {val.kind}"
        )
        assert val.failed_scopes == (), (
            f"SCOPE_CONFLICT failed_scopes 应为 ()，实际: {val.failed_scopes}"
        )

    def test_transformation_loss_default_failed_scopes_empty(self):
        """TRANSFORMATION_LOSS 直接构造后 failed_scopes 默认为 ()。"""
        val = ScoringScopeValidation(
            kind=ScoringValidationKind.TRANSFORMATION_LOSS,
            needs_review=True,
            note="折算损失",
            total=80.0,
        )
        assert val.failed_scopes == (), (
            f"TRANSFORMATION_LOSS failed_scopes 应默认为 ()，实际: {val.failed_scopes}"
        )


# ---------------------------------------------------------------------------
# 3. 空 fact set 与声明 scope
# ---------------------------------------------------------------------------

class TestEmptyFactSetScopes:
    """RED-3: 空 fact set 行为及 declared_scope_expectations 的 fail-visible。"""

    def test_empty_factset_without_declared_scope_failed_scopes_empty(self):
        """空 fact set 且无声明 scope → failed_scopes 为空（无法归因到具体 scope）。"""
        val = validate_scoring_fact_set(ScoringFactSet(source_id="empty"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP
        assert val.failed_scopes == (), (
            f"无声明 scope 时 failed_scopes 应为 ()，实际: {val.failed_scopes}"
        )

    def test_empty_factset_with_declared_scope_is_fail_visible(self):
        """显式 declared_scope_expectations 声明的 scope，即使零事实也必须出现在 failed_scopes。"""
        declared: dict[ScopeIdentity, ScoringScopeExpectation] = {
            ("__single__", "lot_declared"): ScoringScopeExpectation(expected_total=100.0),
        }
        val = validate_scoring_fact_set(
            ScoringFactSet(source_id="empty_decl"),
            declared_scope_expectations=declared,
        )
        assert ("__single__", "lot_declared") in val.failed_scopes, (
            f"已声明 scope 应出现在 failed_scopes，实际: {val.failed_scopes}"
        )


# ---------------------------------------------------------------------------
# 4. expected_by_scope 是运行时只读 Mapping；ScoringScopeExpectation 冻结
# ---------------------------------------------------------------------------

class TestExpectedByScopeReadonly:
    """RED-4: expected_by_scope 运行时不可写，ScoringScopeExpectation 冻结。"""

    def test_expected_by_scope_is_readonly_at_runtime(self):
        """expected_by_scope 不支持 __setitem__，尝试写入必须抛出异常。"""
        items = [_item(f"x{i}", 10.0) for i in range(10)]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="ro"))
        with pytest.raises((TypeError, AttributeError)):
            val.expected_by_scope[("k", "v")] = None  # type: ignore[index]

    def test_scope_expectation_has_expected_total_field(self):
        """ScoringScopeExpectation 有 expected_total: float | None 字段。"""
        exp = ScoringScopeExpectation(expected_total=100.0)
        assert exp.expected_total == 100.0

    def test_scope_expectation_has_expected_by_volume_field(self):
        """ScoringScopeExpectation 有 expected_by_volume: tuple[tuple[str,float],...] 字段。"""
        exp = ScoringScopeExpectation(expected_by_volume=(("technical", 100.0),))
        assert exp.expected_by_volume == (("technical", 100.0),)

    def test_scope_expectation_requires_at_least_one_valid_field(self):
        """ScoringScopeExpectation() 无任何有效字段时必须抛出 ValueError。"""
        with pytest.raises((ValueError, TypeError)):
            ScoringScopeExpectation()


# ---------------------------------------------------------------------------
# 5. expected_per_lot=None → expected_by_scope 空；显式值写入
# ---------------------------------------------------------------------------

class TestExpectedPerLotNone:
    """RED-5: expected_per_lot=None 不写入 expected_by_scope；显式传值则写入。"""

    def test_none_expected_per_lot_produces_empty_expected_by_scope(self):
        """expected_per_lot=None 时 expected_by_scope 为空 Mapping（不以默认100为权威）。"""
        items = [_item(f"x{i}", 10.0) for i in range(10)]
        val = validate_scoring_fact_set(
            build_scoring_fact_set(items, source_id="none_exp"),
            expected_per_lot=None,
        )
        assert len(val.expected_by_scope) == 0, (
            f"expected_per_lot=None 时 expected_by_scope 应为空，实际: {dict(val.expected_by_scope)}"
        )

    def test_explicit_120_per_lot_produces_expected_by_scope_with_120(self):
        """expected_per_lot=120.0 → 对应 scope 写入 expected_by_scope，expected_total=120。"""
        items = [_item(f"x{i}", 12.0) for i in range(10)]
        val = validate_scoring_fact_set(
            build_scoring_fact_set(items, source_id="exp120"),
            expected_per_lot=120.0,
        )
        assert len(val.expected_by_scope) > 0, (
            "expected_per_lot=120.0 时 expected_by_scope 应非空"
        )
        for exp in val.expected_by_scope.values():
            assert exp.expected_total == 120.0, (
                f"写入的期望总分应为 120，实际: {exp.expected_total}"
            )


# ---------------------------------------------------------------------------
# 6. declared_scope_expectations: 零事实 scope fail-visible，写入 expected_by_scope
# ---------------------------------------------------------------------------

class TestDeclaredScopeExpectations:
    """RED-6: 显式声明 scope 的零事实 fail-visible 与 expected_by_scope 写入。"""

    def test_declared_zero_fact_scope_in_failed_scopes(self):
        """声明了 scope 但无对应事实时，该 scope 必须出现在 failed_scopes。"""
        items = [_item(f"x{i}", 10.0, lot="lot_a") for i in range(6)]
        declared: dict[ScopeIdentity, ScoringScopeExpectation] = {
            ("__single__", "lot_a"): ScoringScopeExpectation(expected_total=100.0),
            ("__single__", "lot_b"): ScoringScopeExpectation(expected_total=100.0),
        }
        val = validate_scoring_fact_set(
            build_scoring_fact_set(items, source_id="decl_t"),
            declared_scope_expectations=declared,
        )
        assert ("__single__", "lot_b") in val.failed_scopes, (
            f"零事实的声明 scope lot_b 应在 failed_scopes，实际: {val.failed_scopes}"
        )

    def test_declared_scope_expectations_written_to_expected_by_scope(self):
        """declared_scope_expectations 的内容应写入 expected_by_scope。"""
        items = [_item(f"x{i}", 10.0, lot="lot_a") for i in range(10)]
        declared: dict[ScopeIdentity, ScoringScopeExpectation] = {
            ("__single__", "lot_a"): ScoringScopeExpectation(expected_total=100.0),
        }
        val = validate_scoring_fact_set(
            build_scoring_fact_set(items, source_id="decl_exp"),
            declared_scope_expectations=declared,
        )
        assert ("__single__", "lot_a") in val.expected_by_scope, (
            "declared scope 应出现在 expected_by_scope"
        )
        assert val.expected_by_scope[("__single__", "lot_a")].expected_total == 100.0


# ---------------------------------------------------------------------------
# 7. 旧门禁不放宽
# ---------------------------------------------------------------------------

class TestOldGuardsPreserved:
    """RED-7: base_score/price_formula 零分仍 EVIDENCE_GAP；bonus/penalty 零值不缺失。"""

    def test_zero_base_score_triggers_evidence_gap(self):
        """fact_type=base_score 且 score_weight=0 → EVIDENCE_GAP（保持旧门禁）。"""
        items = [
            _item("有效项", 100.0, fact_type="base_score"),
            _item("缺失项", 0.0, fact_type="base_score"),
        ]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="zero_b"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP, (
            f"base_score 零分值应为 EVIDENCE_GAP，实际: {val.kind}"
        )

    def test_zero_price_formula_triggers_evidence_gap(self):
        """fact_type=price_formula 且 score_weight=0 → EVIDENCE_GAP。"""
        items = [
            _item("价格公式项", 0.0, fact_type="price_formula"),
            _item("其他项", 100.0, fact_type="base_score"),
        ]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="zero_p"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP, (
            f"price_formula 零分值应为 EVIDENCE_GAP，实际: {val.kind}"
        )

    def test_zero_bonus_not_treated_as_missing(self):
        """fact_type=bonus 且 score_weight=0 不算缺失，scope 合计满足期望时为 COMPLETE。"""
        items = (
            [_item(f"x{i}", 10.0, fact_type="base_score") for i in range(10)]
            + [_item("加分项", 0.0, fact_type="bonus")]
        )
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="bonus_z"))
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"bonus 零分不应触发 EVIDENCE_GAP，实际: {val.kind}"
        )

    def test_zero_penalty_not_treated_as_missing(self):
        """fact_type=penalty 且 score_weight=0 不算缺失。"""
        items = (
            [_item(f"x{i}", 10.0, fact_type="base_score") for i in range(10)]
            + [_item("扣分项", 0.0, fact_type="penalty")]
        )
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="penalty_z"))
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"penalty 零分不应触发 EVIDENCE_GAP，实际: {val.kind}"
        )

    def test_multi_scheme_multi_lot_each_scope_independently(self):
        """多 scheme + 多 lot，各 scope 独立校验，均满足 100 → COMPLETE。"""
        items = (
            [_item(f"a{i}", 10.0, lot="lot_a", scheme="scheme_1") for i in range(10)]
            + [_item(f"b{i}", 10.0, lot="lot_b", scheme="scheme_2") for i in range(10)]
        )
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="multi_s"))
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"双 scheme 双 lot 各合计100时应为 COMPLETE，实际: {val.kind}"
        )

    def test_non_100_scale_complete_with_explicit_expected(self):
        """非 100 分制：expected_per_lot=120，scope 合计 120 → COMPLETE。"""
        items = [_item(f"x{i}", 12.0) for i in range(10)]
        val = validate_scoring_fact_set(
            build_scoring_fact_set(items, source_id="scale120"),
            expected_per_lot=120.0,
        )
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"120分制合计120应为 COMPLETE，实际: {val.kind}"
        )


# ---------------------------------------------------------------------------
# 8. 卷权重和门禁（C.6）
# ---------------------------------------------------------------------------

class TestVolumeWeightGuard:
    """RED-C: 卷权重和不等于1时仍需复核；折算制权重和=1时 COMPLETE。"""

    def test_single_volume_weight_not_1_triggers_evidence_gap(self):
        """单卷 volume_weight=0.4（权重和≠1），事实合计=100 → EVIDENCE_GAP（旧门禁）。"""
        items = [_item("技术项", 100.0, volume="technical",
                       volume_weight=0.4, volume_max=100.0)]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="wt_single"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP, (
            f"卷权重和=0.4≠1 应触发 EVIDENCE_GAP，实际: {val.kind}"
        )

    def test_three_volumes_weight_sum_1_is_complete(self):
        """三卷权重 0.1+0.5+0.4=1.0，各卷事实满足声明基准 → COMPLETE。"""
        items = [
            _item("技术", 100.0, volume="technical", volume_weight=0.1, volume_max=100.0),
            _item("价格", 100.0, volume="price",     volume_weight=0.5, volume_max=100.0),
            _item("服务", 100.0, volume="service",   volume_weight=0.4, volume_max=100.0),
        ]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="wt_three"))
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"权重和=1.0 且各卷合计满足基准应为 COMPLETE，实际: {val.kind}"
        )

    def test_zero_bonus_still_legal_with_volume_weight(self):
        """bonus 零分值 + 同卷主项权重合法 → 不触发 EVIDENCE_GAP（旧门禁延续）。"""
        items = (
            [_item(f"x{i}", 10.0, fact_type="base_score",
                   volume="technical", volume_weight=0.5, volume_max=100.0)
             for i in range(10)]
            + [_item("奖励项", 0.0, fact_type="bonus",
                     volume="price", volume_weight=0.5, volume_max=100.0)]
            + [_item("价格主项", 100.0, fact_type="base_score",
                     volume="price", volume_weight=0.5, volume_max=100.0)]
        )
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="bonus_wt"))
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"bonus 零分合法，主项满足基准应为 COMPLETE，实际: {val.kind}"
        )


# ---------------------------------------------------------------------------
# 9. 部分卷权重
# ---------------------------------------------------------------------------

class TestPartialVolumeWeight:
    """RED-9: 部分 volume 有 volume_weight 时，只要总分闭合，不得触发 EVIDENCE_GAP。

 只有 set(volume_weights)==set(by_volume) 才启用权重和 ≈1 门禁；
 partial coverage 降级到 volume_max/总分逻辑。
 """

    def test_four_volumes_partial_weight_total_100_is_complete(self):
        """4 卷各 10/35/45/10 合计 100，仅 technical 有 volume_weight=0.35 → COMPLETE。

 原: partial 门禁误返回 EVIDENCE_GAP；修复后应为 COMPLETE。
 """
        items = [
            _item("技术项", 35.0, volume="technical", volume_weight=0.35),
            _item("商务项", 10.0, volume="commercial"),
            _item("服务项", 10.0, volume="service"),
            _item("价格项", 45.0, volume="price"),
        ]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="partial_wt_100"))
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"部分卷有权重但总分=100 应为 COMPLETE，实际: {val.kind}"
        )

    def test_partial_weight_total_not_closed_is_evidence_gap(self):
        """部分卷有权重，但总分不满足期望 → 仍为 EVIDENCE_GAP（总分门禁不放宽）。"""
        items = [
            _item("技术项", 35.0, volume="technical", volume_weight=0.35),
            _item("商务项", 10.0, volume="commercial"),
            # 总分 45，期望 100
        ]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="partial_wt_gap"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP, (
            f"部分卷有权重但总分不闭合仍应为 EVIDENCE_GAP，实际: {val.kind}"
        )

    def test_all_volumes_weight_not_sum_to_1_still_fails(self):
        """所有卷均有权重但 sum≠1 → EVIDENCE_GAP（权重和门禁保留）。"""
        items = [
            _item("技术项", 35.0, volume="technical", volume_weight=0.35),
            _item("价格项", 65.0, volume="price", volume_weight=0.50),  # sum=0.85≠1
        ]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="all_wt_bad_sum"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP, (
            f"全卷权重和=0.85≠1 应为 EVIDENCE_GAP，实际: {val.kind}"
        )


# ---------------------------------------------------------------------------
# 10. 独立百分制识别（, 与 score_totals 报告层对称）
# ---------------------------------------------------------------------------

class TestIndependentHundredVolumes:
    """: 无卷基准/无卷权重且 ≥2 明确卷各闭合 100 时判定 COMPLETE。

 根因回归: 项目2231三卷各100（合计300）在报告层判通过，但 scope 校验层
 落到全局 fallback 拿 300 比 100 误判缺口，触发 L3 空转 180s。
 """

    def test_three_independent_100_volumes_complete(self):
        """三卷（技术/商务/资信）各 100 → COMPLETE，无失败 scope（项目2231场景）。"""
        items = (
            [_item(f"t{i}", 20.0, volume="technical") for i in range(5)]
            + [_item(f"c{i}", 25.0, volume="commercial") for i in range(4)]
            + [_item(f"q{i}", 50.0, volume="qualification") for i in range(2)]
        )
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="ind100_3vol"))
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"三卷各100独立百分制应为 COMPLETE，实际: {val.kind} note={val.note}"
        )
        assert val.failed_scopes == (), f"不应有失败 scope: {val.failed_scopes}"
        assert not val.needs_review

    def test_one_volume_off_100_still_evidence_gap(self):
        """资信卷 96（修复前的吞行坏状态）→ 仍须 EVIDENCE_GAP，不得静默放行。"""
        items = (
            [_item(f"t{i}", 20.0, volume="technical") for i in range(5)]
            + [_item(f"c{i}", 25.0, volume="commercial") for i in range(4)]
            + [_item("q0", 96.0, volume="qualification")]
        )
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="ind100_96"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP, (
            f"资信卷96≠100 应保持 EVIDENCE_GAP fail-visible，实际: {val.kind}"
        )

    def test_unspecified_volume_blocks_shortcut(self):
        """存在未标卷条目时不得走独立百分制捷径（归属不明不能证明各卷闭合）。"""
        items = (
            [_item(f"t{i}", 50.0, volume="technical") for i in range(2)]
            + [_item(f"c{i}", 50.0, volume="commercial") for i in range(2)]
            + [_item("孤儿项", 100.0, volume="")]
        )
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="ind100_unspec"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP, (
            f"含 unspecified 卷不应走独立百分制捷径，实际: {val.kind}"
        )

    def test_declared_non_100_expectation_blocks_shortcut(self):
        """权威声明期望 250 时（期望≠100）双卷各100不得判闭环。"""
        scope: ScopeIdentity = ("__single__", "__single__")
        items = (
            [_item(f"t{i}", 50.0, volume="technical") for i in range(2)]
            + [_item(f"c{i}", 50.0, volume="commercial") for i in range(2)]
        )
        val = validate_scoring_fact_set(
            build_scoring_fact_set(items, source_id="ind100_exp250"),
            declared_scope_expectations={
                scope: ScoringScopeExpectation(expected_total=250.0)
            },
        )
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP, (
            f"声明期望250≠100 不应走独立百分制捷径，实际: {val.kind}"
        )

    def test_weighted_hundred_volumes_sum_1_complete(self):
        """加权百分制: 双卷各100且权重和=1 → COMPLETE（遗留事项③, 与报告层对称）。"""
        items = [
            _item("技术", 100.0, volume="technical", volume_weight=0.5),
            _item("价格", 100.0, volume="price", volume_weight=0.5),
        ]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="ind100_wt"))
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"加权百分制各卷100权重和1应为 COMPLETE，实际: {val.kind} note={val.note}"
        )

    def test_weighted_hundred_volumes_bad_weight_sum_still_gap(self):
        """加权百分制各卷100但权重和=0.85≠1 → 权重和门禁仍 fail-visible。"""
        items = [
            _item("技术", 100.0, volume="technical", volume_weight=0.35),
            _item("价格", 100.0, volume="price", volume_weight=0.50),
        ]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="ind100_wt_bad"))
        assert val.kind == ScoringValidationKind.EVIDENCE_GAP, (
            f"权重和0.85≠1 应保持 EVIDENCE_GAP，实际: {val.kind}"
        )

    def test_partial_weight_hundred_volumes_complete(self):
        """部分卷有权重的双卷各100 → 捷径同样适用（报告层独立百分制不检查权重）。"""
        items = [
            _item("技术", 100.0, volume="technical", volume_weight=0.6),
            _item("价格", 100.0, volume="price"),
        ]
        val = validate_scoring_fact_set(build_scoring_fact_set(items, source_id="ind100_wt_part"))
        assert val.kind == ScoringValidationKind.COMPLETE, (
            f"部分权重双卷各100应为 COMPLETE，实际: {val.kind} note={val.note}"
        )

"""Task-2 RED: 完整候选契约测试。

职责: 验证 FullCandidateEvidenceRef/Fact/ScoringBasis/Scope/Request 不变量与 extra 拒绝。
不做: 不测试 LLM、数据库、API 路由或真实提取流程。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_full_candidate_contracts import (  # noqa: E402
    FullCandidateEvidenceRef,
    FullCandidateFact,
    FullCandidateRequest,
    FullCandidateScope,
    FullCandidateScoringBasis,
)
from app.core.extractor.scoring.scoring_scope_contracts import (  # noqa: E402
    ScoringScopeExpectation,
)

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 辅助构建
# ---------------------------------------------------------------------------


def _make_ref(
    chunk_id: int = 1,
    page: int = 5,
    quote: str = "满分100分",
    fields: tuple = ("score_weight",),
) -> FullCandidateEvidenceRef:
    return FullCandidateEvidenceRef(
        source_chunk_id=chunk_id,
        source_page=page,
        evidence_quote=quote,
        supports_fields=fields,
    )


def _make_fact(evidence_refs: tuple | None = None) -> FullCandidateFact:
    if evidence_refs is None:
        evidence_refs = (_make_ref(),)
    return FullCandidateFact(
        category="技术评分",
        score_item_name="业绩",
        description="近3年业绩",
        score_weight=20.0,
        fact_type="base_score",
        volume="technical",
        volume_label="技术卷",
        score_rules=("完成2项得10分", "完成4项得20分"),
        deduction_rules=(),
        evidence_refs=evidence_refs,
    )


def _make_basis(total: float = 100.0) -> FullCandidateScoringBasis:
    return FullCandidateScoringBasis(expected_total=total)


def _make_scope(facts: tuple | None = None) -> FullCandidateScope:
    return FullCandidateScope(
        scheme="__single__",
        lot="__single__",
        scoring_basis=_make_basis(),
        facts=facts if facts is not None else (_make_fact(),),
    )


def _make_bundle(target_scope: tuple = ("__single__", "__single__")) -> SimpleNamespace:
    return SimpleNamespace(target_scope=target_scope)


def _make_request(
    target_scope: tuple = ("__single__", "__single__"),
    budget: int = 24000,
    max_tokens: int = 5000,
    bundle_scope: tuple | None = None,
) -> FullCandidateRequest:
    if bundle_scope is None:
        bundle_scope = target_scope
    return FullCandidateRequest(
        target_scope=target_scope,
        current_items=(_make_fact(),),
        volume_defs=(("technical", 100.0),),
        scope_expectation=ScoringScopeExpectation(expected_total=100.0),
        diagnostics=(),
        evidence_bundle=_make_bundle(bundle_scope),
        context_char_budget=budget,
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# A. FullCandidateEvidenceRef
# ---------------------------------------------------------------------------


class TestFullCandidateEvidenceRef:
    def test_valid_ref_fields(self) -> None:
        ref = _make_ref()
        assert ref.source_chunk_id == 1
        assert ref.source_page == 5
        assert ref.evidence_quote == "满分100分"
        assert "score_weight" in ref.supports_fields

    def test_chunk_id_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_ref(chunk_id=0)

    def test_chunk_id_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_ref(chunk_id=-5)

    def test_page_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_ref(page=0)

    def test_empty_quote_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_ref(quote="")

    def test_whitespace_only_quote_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_ref(quote="   ")

    def test_empty_supports_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_ref(fields=())

    def test_duplicate_supports_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_ref(fields=("score_weight", "score_weight"))

    def test_invalid_supports_field_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_ref(fields=("nonexistent_field",))

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateEvidenceRef(
                source_chunk_id=1,
                source_page=1,
                evidence_quote="test",
                supports_fields=("category",),
                unknown_extra="bad",
            )

    def test_multiple_valid_supports_fields(self) -> None:
        ref = _make_ref(fields=("category", "score_item_name", "description"))
        assert len(ref.supports_fields) == 3

    def test_all_allowed_field_names_valid(self) -> None:
        all_fields = (
            "category", "score_item_name", "description", "score_weight",
            "fact_type", "volume", "volume_label", "score_rules", "deduction_rules",
        )
        ref = _make_ref(fields=all_fields)
        assert len(ref.supports_fields) == len(all_fields)


# ---------------------------------------------------------------------------
# B. FullCandidateFact
# ---------------------------------------------------------------------------


class TestFullCandidateFact:
    def test_valid_fact(self) -> None:
        fact = _make_fact()
        assert fact.score_item_name == "业绩"
        assert len(fact.evidence_refs) == 1

    def test_empty_evidence_refs_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_fact(evidence_refs=())

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateFact(
                category="TECH",
                score_item_name="x",
                description="y",
                score_weight=10.0,
                fact_type="base_score",
                volume="technical",
                volume_label="技术卷",
                score_rules=(),
                deduction_rules=(),
                evidence_refs=(_make_ref(),),
                extra_bad="oops",
            )

    def test_derived_source_pages(self) -> None:
        ref1 = _make_ref(chunk_id=1, page=3)
        ref2 = _make_ref(chunk_id=2, page=7)
        fact = _make_fact(evidence_refs=(ref1, ref2))
        assert 3 in fact.source_pages
        assert 7 in fact.source_pages

    def test_derived_source_chunk_ids(self) -> None:
        ref1 = _make_ref(chunk_id=10, page=1)
        ref2 = _make_ref(chunk_id=20, page=2)
        fact = _make_fact(evidence_refs=(ref1, ref2))
        assert 10 in fact.source_chunk_ids
        assert 20 in fact.source_chunk_ids

    def test_derived_evidence_quote_returns_first_ref(self) -> None:
        ref = _make_ref(quote="原始引用文本")
        fact = _make_fact(evidence_refs=(ref,))
        assert fact.evidence_quote == "原始引用文本"

    def test_source_pages_deduped(self) -> None:
        ref1 = _make_ref(chunk_id=1, page=5)
        ref2 = _make_ref(chunk_id=2, page=5)  # same page
        fact = _make_fact(evidence_refs=(ref1, ref2))
        assert fact.source_pages.count(5) == 1

    def test_source_chunk_ids_deduped(self) -> None:
        ref1 = _make_ref(chunk_id=7, page=1)
        ref2 = _make_ref(chunk_id=7, page=2)  # same chunk
        fact = _make_fact(evidence_refs=(ref1, ref2))
        assert fact.source_chunk_ids.count(7) == 1


# ---------------------------------------------------------------------------
# C. FullCandidateScoringBasis
# ---------------------------------------------------------------------------


class TestFullCandidateScoringBasis:
    def test_valid_with_expected_total(self) -> None:
        basis = _make_basis(100.0)
        assert basis.expected_total == 100.0
        assert basis.expected_by_volume == ()

    def test_valid_with_expected_by_volume(self) -> None:
        basis = FullCandidateScoringBasis(
            expected_by_volume=(("technical", 60.0), ("commercial", 40.0))
        )
        assert len(basis.expected_by_volume) == 2

    def test_neither_valid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScoringBasis()

    def test_total_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScoringBasis(expected_total=0.0)

    def test_total_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScoringBasis(expected_total=-10.0)

    def test_duplicate_volume_keys_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScoringBasis(
                expected_by_volume=(("technical", 60.0), ("technical", 40.0))
            )

    def test_empty_volume_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScoringBasis(expected_by_volume=(("", 60.0),))

    def test_volume_value_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScoringBasis(expected_by_volume=(("technical", 0.0),))

    def test_volume_value_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScoringBasis(expected_by_volume=(("technical", -5.0),))

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScoringBasis(expected_total=100.0, unexpected_field="bad")

    def test_from_scope_expectation_total(self) -> None:
        exp = ScoringScopeExpectation(expected_total=120.0)
        basis = FullCandidateScoringBasis.from_scope_expectation(exp)
        assert basis.expected_total == 120.0
        assert basis.expected_by_volume == ()

    def test_from_scope_expectation_by_volume(self) -> None:
        exp = ScoringScopeExpectation(
            expected_by_volume=(("technical", 60.0), ("commercial", 40.0))
        )
        basis = FullCandidateScoringBasis.from_scope_expectation(exp)
        assert len(basis.expected_by_volume) == 2
        assert basis.expected_total is None

    def test_from_scope_expectation_both(self) -> None:
        exp = ScoringScopeExpectation(expected_total=100.0)
        basis = FullCandidateScoringBasis.from_scope_expectation(exp)
        assert basis.expected_total == 100.0


# ---------------------------------------------------------------------------
# D. FullCandidateScope
# ---------------------------------------------------------------------------


class TestFullCandidateScope:
    def test_valid_scope_with_facts(self) -> None:
        scope = _make_scope()
        assert scope.scheme == "__single__"
        assert len(scope.facts) == 1

    def test_empty_facts_valid(self) -> None:
        # 空 facts 表示模型无法完成，合法
        scope = _make_scope(facts=())
        assert scope.facts == ()

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScope(
                scheme="A",
                lot="L1",
                scoring_basis=_make_basis(),
                facts=(),
                declared_total=None,
                rogue_field="bad",
            )

    def test_declared_total_optional(self) -> None:
        scope = FullCandidateScope(
            scheme="A",
            lot="L1",
            scoring_basis=_make_basis(),
            facts=(),
            declared_total=95.0,
        )
        assert scope.declared_total == 95.0

    def test_declared_total_none_valid(self) -> None:
        scope = _make_scope()
        assert scope.declared_total is None

    def test_nested_basis_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScope(
                scheme="A",
                lot="L1",
                scoring_basis=FullCandidateScoringBasis(
                    expected_total=100.0,
                    sneaky_extra="bad",  # type: ignore[call-arg]
                ),
                facts=(),
            )


# ---------------------------------------------------------------------------
# E. FullCandidateRequest
# ---------------------------------------------------------------------------


class TestFullCandidateRequest:
    def test_valid_request_construction(self) -> None:
        req = _make_request()
        assert req.target_scope == ("__single__", "__single__")
        assert req.context_char_budget == 24000
        assert req.max_tokens == 5000

    def test_budget_zero_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_request(budget=0)

    def test_budget_negative_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_request(budget=-1)

    def test_max_tokens_zero_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_request(max_tokens=0)

    def test_max_tokens_negative_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_request(max_tokens=-100)

    def test_scope_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_request(
                target_scope=("A", "L1"),
                bundle_scope=("A", "L2"),
            )

    def test_frozen_immutable(self) -> None:
        req = _make_request()
        with pytest.raises((AttributeError, TypeError)):
            req.context_char_budget = 999  # type: ignore[misc]

    def test_scope_expectation_preserved(self) -> None:
        req = _make_request()
        assert req.scope_expectation is not None
        assert req.scope_expectation.expected_total == 100.0

    def test_current_items_copied_as_tuple(self) -> None:
        fact = _make_fact()
        req = FullCandidateRequest(
            target_scope=("__single__", "__single__"),
            current_items=(fact,),
            volume_defs=(("technical", 100.0),),
            scope_expectation=ScoringScopeExpectation(expected_total=100.0),
            diagnostics=(),
            evidence_bundle=_make_bundle(),
            context_char_budget=10000,
            max_tokens=1000,
        )
        assert len(req.current_items) == 1
        assert isinstance(req.current_items, tuple)

    def test_multi_scope_single_target(self) -> None:
        # 每次 request 只对应一个 scheme×lot
        req = _make_request(target_scope=("scheme_A", "lot_1"))
        assert req.target_scope == ("scheme_A", "lot_1")


# ---------------------------------------------------------------------------
# E. 已知形变归一（: 真实模型输出的常见合法形变必须被归一而非拒绝）
# ---------------------------------------------------------------------------


def _fact_payload(**overrides) -> dict:
    payload = {
        "category": "TECH",
        "score_item_name": "总体设计方案",
        "description": "总体设计思路",
        "score_weight": 15.0,
        "fact_type": "base_score",
        "volume": "technical",
        "volume_label": "技术部分评分标准",
        "score_rules": ["按方案完整性评分"],
        "deduction_rules": [],
        "evidence_refs": [
            {
                "source_chunk_id": 170814,
                "source_page": 25,
                "evidence_quote": "总体设计方案（15分）",
                "supports_fields": ["score_weight"],
            }
        ],
    }
    payload.update(overrides)
    return payload


class TestKnownShapeNormalization:
    """项目2035 L3影子日志中观察到的qwen-max真实形变, 归一后必须通过校验。"""

    def test_basis_dict_expected_by_volume_normalized(self) -> None:
        basis = FullCandidateScoringBasis.model_validate(
            {"expected_total": 100.0, "expected_by_volume": {"technical": 57.0, "price": 30.0}}
        )
        assert dict(basis.expected_by_volume) == {"technical": 57.0, "price": 30.0}

    def test_basis_flat_pair_expected_by_volume_normalized(self) -> None:
        basis = FullCandidateScoringBasis.model_validate(
            {"expected_by_volume": ["commercial", 13.0]}
        )
        assert basis.expected_by_volume == (("commercial", 13.0),)

    def test_basis_flat_volume_keys_folded(self) -> None:
        basis = FullCandidateScoringBasis.model_validate(
            {"expected_total": 100.0, "price": 30.0, "technical": 57.0, "commercial": 13.0}
        )
        assert dict(basis.expected_by_volume) == {
            "price": 30.0, "technical": 57.0, "commercial": 13.0,
        }

    def test_fact_string_rules_wrapped(self) -> None:
        fact = FullCandidateFact.model_validate(
            _fact_payload(score_rules="投标报价满分30分", deduction_rules="")
        )
        assert fact.score_rules == ("投标报价满分30分",)
        assert fact.deduction_rules == ()

    def test_fact_redundant_metadata_keys_stripped(self) -> None:
        fact = FullCandidateFact.model_validate(
            _fact_payload(
                lot=None, scheme="scheme_1", volume_weight=0.3,
                volume_max=30.0, source_pages="25, 26",
            )
        )
        assert fact.score_item_name == "总体设计方案"

    def test_fact_unknown_field_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateFact.model_validate(_fact_payload(hallucinated_field="bad"))

    def test_evidence_ref_string_supports_fields_wrapped(self) -> None:
        ref = FullCandidateEvidenceRef.model_validate(
            {
                "source_chunk_id": 1,
                "source_page": 5,
                "evidence_quote": "满分30分",
                "supports_fields": "score_weight",
            }
        )
        assert ref.supports_fields == ("score_weight",)

    def test_scope_stray_string_facts_filtered_and_shared_stripped(self) -> None:
        scope = FullCandidateScope.model_validate(
            {
                "scheme": "scheme_1",
                "lot": "__single__",
                "scoring_basis": {"expected_total": 100.0},
                "facts": [_fact_payload(), "score_weight", "volume", "volume_label"],
                "shared": False,
            }
        )
        assert len(scope.facts) == 1
        assert scope.facts[0].score_item_name == "总体设计方案"

    def test_scope_unknown_field_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FullCandidateScope.model_validate(
                {
                    "scheme": "scheme_1",
                    "lot": "__single__",
                    "scoring_basis": {"expected_total": 100.0},
                    "facts": [],
                    "made_up_key": 1,
                }
            )


# ---------------------------------------------------------------------------
# F. 平铺证据归一（: 项目2231 L3终端日志观察到的qwen-max真实形变）
# ---------------------------------------------------------------------------


class TestFlattenedEvidenceNormalization:
    """: 约束解码不可用时模型把证据字段平铺到 fact 顶层, 契约层须归一或 fail-visible。"""

    def test_flattened_evidence_with_garbage_refs_lifted(self) -> None:
        """真实形状golden: evidence_refs=':[{' 垃圾串 + 三平铺键齐备 → 提升为单条引用。"""
        fact = FullCandidateFact.model_validate(
            _fact_payload(
                evidence_refs=":[{",
                source_chunk_id=177845,
                source_page=1,
                evidence_quote="设计任务书优化：……设计任务书优化",
            )
        )
        assert len(fact.evidence_refs) == 1
        ref = fact.evidence_refs[0]
        assert ref.source_chunk_id == 177845
        assert ref.source_page == 1
        assert ref.evidence_quote == "设计任务书优化：……设计任务书优化"
        # 合成声明含基础必需字段 + fact 实有的 volume_label/score_rules
        assert {"score_item_name", "score_weight", "description"} <= set(ref.supports_fields)
        assert "volume_label" in ref.supports_fields
        assert "score_rules" in ref.supports_fields
        assert "deduction_rules" not in ref.supports_fields  # fact 无扣分规则不声明

    def test_flattened_evidence_without_refs_key_lifted(self) -> None:
        """evidence_refs 整体缺失但三平铺键齐备 → 同样提升。"""
        payload = _fact_payload(
            source_chunk_id=170814,
            source_page=25,
            evidence_quote="总体设计方案（15分）",
        )
        payload.pop("evidence_refs")
        fact = FullCandidateFact.model_validate(payload)
        assert fact.evidence_refs[0].source_chunk_id == 170814

    def test_valid_refs_with_redundant_flattened_echo_stripped(self) -> None:
        """evidence_refs 合法非空时, 顶层平铺键属冗余回显 → 剥离且不覆盖结构化引用。"""
        fact = FullCandidateFact.model_validate(
            _fact_payload(
                source_chunk_id=999999,
                source_page=99,
                evidence_quote="冗余回显不得覆盖结构化引用",
            )
        )
        assert len(fact.evidence_refs) == 1
        assert fact.evidence_refs[0].source_chunk_id == 170814  # 保留原结构化引用

    def test_partial_flattened_keys_stay_fail_visible(self) -> None:
        """平铺键不齐备(缺 source_chunk_id)时不归一 → extra_forbidden fail-visible。"""
        payload = _fact_payload(
            evidence_refs=":[{",
            source_page=1,
            evidence_quote="残缺平铺",
        )
        with pytest.raises(ValidationError):
            FullCandidateFact.model_validate(payload)

    def test_flattened_wrong_types_stay_fail_visible(self) -> None:
        """平铺键类型错误(chunk_id 为字符串)时不归一 → fail-visible。"""
        payload = _fact_payload(
            evidence_refs=":[{",
            source_chunk_id="177845",
            source_page=1,
            evidence_quote="类型错误",
        )
        with pytest.raises(ValidationError):
            FullCandidateFact.model_validate(payload)

    def test_garbage_refs_without_flattened_keys_stay_fail_visible(self) -> None:
        """evidence_refs 垃圾串且无平铺键可提升 → 保持校验失败, 不静默吞。"""
        payload = _fact_payload(evidence_refs=":[{")
        with pytest.raises(ValidationError):
            FullCandidateFact.model_validate(payload)

    def test_stringified_json_refs_deserialized(self) -> None:
        """(项目2306真实形变): evidence_refs 被 stringify 成 JSON 字符串
 但内容完整正确 → 反序列化后正常通过校验。"""
        import json

        refs = [
            {
                "source_chunk_id": 180001,
                "source_page": 1,
                "evidence_quote": "企业规模评审：根据纳税总金额排序评分",
                "supports_fields": ["score_weight", "description"],
            }
        ]
        fact = FullCandidateFact.model_validate(
            _fact_payload(evidence_refs=json.dumps(refs, ensure_ascii=False))
        )
        assert len(fact.evidence_refs) == 1
        assert fact.evidence_refs[0].source_chunk_id == 180001
        assert fact.evidence_refs[0].source_page == 1

    def test_stringified_garbage_json_stays_fail_visible(self) -> None:
        """字符串化但内容非法(截断/垃圾)且无平铺键 → 保持 fail-visible 不静默吞。"""
        payload = _fact_payload(evidence_refs='[{"source_chunk_id": 180')
        with pytest.raises(ValidationError):
            FullCandidateFact.model_validate(payload)

    def test_stringified_refs_with_invalid_inner_shape_fail_visible(self) -> None:
        """字符串化 JSON 可解析但内层结构不合法(缺必需字段) → 校验拒绝。"""
        payload = _fact_payload(evidence_refs='[{"source_chunk_id": 1}]')
        with pytest.raises(ValidationError):
            FullCandidateFact.model_validate(payload)

    def test_scope_level_flattened_facts_validate_end_to_end(self) -> None:
        """终端日志完整形状: scope 内 facts 全部平铺 → 整体归一通过并保留全部事实。"""
        scope = FullCandidateScope.model_validate(
            {
                "scheme": "scheme_1",
                "lot": "__single__",
                "scoring_basis": {"expected_total": 100.0},
                "facts": [
                    _fact_payload(
                        evidence_refs=":[{",
                        source_chunk_id=177845,
                        source_page=1,
                        evidence_quote="设计任务书优化",
                        score_item_name="设计任务书优化",
                    ),
                    _fact_payload(),  # 正常结构化事实混排
                ],
            }
        )
        assert len(scope.facts) == 2
        assert scope.facts[0].evidence_refs[0].source_chunk_id == 177845
        assert scope.facts[1].evidence_refs[0].source_chunk_id == 170814

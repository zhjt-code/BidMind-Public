"""阶段8a 未响应确定性判定防御测试。

职责: 锁定 abstain 清单的每一条、 三类真问题的不放行、off/shadow/enforce
 三态行为，以及 shadow 模式对 LLM 全量路径的零影响。
不做: 不调真实 LLM、不连 DB、不校验 Prompt 文本内容。

夹具说明: 要求文本按存量数据的"形状"重写（句式/术语/长度量级），不含任何真实招标
文件原文，避免把文档内容写进仓库。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Optional

import pytest

from app.core.ai.reviewer_parts import deterministic_no_response as det
from app.core.ai.reviewer_parts.deterministic_no_response import (
    MODE_ENFORCE,
    MODE_OFF,
    MODE_SHADOW,
    DeterministicNoResponseOutcome,
    build_enforced_result,
    evaluate_no_response_pair,
    resolve_mode,
)
from app.core.matcher.requirement_matcher import MatchedPair
from app.core.review_contracts import ReviewEvidenceBundle
from app.models.requirement import RequirementCategory

pytestmark = pytest.mark.scoring

NO_RESPONSE = "[未找到响应]"

# 一条纯实质性、无形式词、无评分词的要求（确定性判定的"应当命中"基准形状）
SUBSTANTIVE_TEXT = "投标人须提供近三年内已完成的同类项目业绩证明材料及对应合同复印件。"


# ---------------------------------------------------------------------------
# 夹具构造
# ---------------------------------------------------------------------------


def _make_requirement(
    *,
    description: str = SUBSTANTIVE_TEXT,
    is_mandatory: int = 0,
    requirement_kind: str = "normal",
    is_key_review: int = 0,
    review_relevance: str = "actionable",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=101,
        description=description,
        category=RequirementCategory.QUAL,
        is_mandatory=is_mandatory,
        requirement_kind=requirement_kind,
        is_key_review=is_key_review,
        review_relevance=review_relevance,
        score_weight=None,
        scoring_metadata=None,
    )


def _make_response(
    *,
    content: str = NO_RESPONSE,
    relevance_note: Optional[str] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=201,
        content=content,
        relevance_note=relevance_note,
        source_pages=None,
        completeness=None,
        missing_elements=None,
        linked_to=None,
        evidence_type=None,
        evidence_anchor=None,
        relevance_score=None,
        document_id=301,
    )


def _make_pair(**req_kwargs) -> MatchedPair:
    response = req_kwargs.pop("response", None) or _make_response()
    return MatchedPair(requirement=_make_requirement(**req_kwargs), response=response)


def _make_bundle(
    *,
    quality_flags: Optional[list[str]] = None,
    image_evidence: str = "",
    cross_category_evidence: str = "",
) -> ReviewEvidenceBundle:
    return ReviewEvidenceBundle(
        response_content=NO_RESPONSE,
        source_pages=[],
        completeness=None,
        missing_elements=[],
        linked_to=None,
        evidence_type=None,
        evidence_anchor=None,
        relevance_score=None,
        relevance_note=None,
        image_evidence=image_evidence,
        cross_category_evidence=cross_category_evidence,
        quality_flags=list(quality_flags or []),
    )


def _evaluate(pair: MatchedPair, bundle=None, mode: str = MODE_SHADOW):
    return evaluate_no_response_pair(pair, bundle or _make_bundle(), mode=mode)


# ---------------------------------------------------------------------------
# 基准: 全部 abstain 条件均未命中时才给出建议
# ---------------------------------------------------------------------------


def test_clean_no_response_pair_yields_not_compliant_suggestion() -> None:
    """基准形状: 未响应 + 证据完备 + actionable + 无形式词 → 判定不符合。"""
    outcome = _evaluate(_make_pair())
    assert outcome.abstained is False, f"不应弃权，实际原因={outcome.abstain_reasons}"
    assert outcome.verdict is not None
    patch = outcome.verdict.suggested_patch
    assert patch["compliance_level"] == "not"
    assert patch["is_compliant"] is False
    assert outcome.needs_review is False


def test_non_mandatory_gets_medium_risk_mandatory_gets_high_risk() -> None:
    """风险等级按否决项分档，与存量干净语料的 HIGH/MEDIUM 分布一致。"""
    assert _evaluate(_make_pair(is_mandatory=0)).verdict.suggested_patch["risk_level"] == "medium"
    assert _evaluate(_make_pair(is_mandatory=1)).verdict.suggested_patch["risk_level"] == "high"


# ---------------------------------------------------------------------------
# abstain 清单: 每条独立用例
# ---------------------------------------------------------------------------


def test_abstain_when_pair_has_response() -> None:
    """有响应场景不属本模块职责，必须弃权。"""
    pair = _make_pair(response=_make_response(content="我方完全响应上述要求，详见第12页。"))
    outcome = _evaluate(pair)
    assert outcome.abstained is True
    assert det.ABSTAIN_HAS_RESPONSE in outcome.abstain_reasons


def test_abstain_on_extraction_failure() -> None:
    """提取阶段失败的占位内容不得被推定为"未响应=不符合"。"""
    from app.core.extractor.extraction_failure import EXTRACTION_FAILED_CONTENT

    pair = _make_pair(response=_make_response(content=EXTRACTION_FAILED_CONTENT))
    outcome = _evaluate(pair)
    assert outcome.abstained is True
    assert det.ABSTAIN_EXTRACTION_FAILED in outcome.abstain_reasons


def test_abstain_on_evidence_insufficient() -> None:
    """分面缺口/饥饿落库的证据不足标记不得被 enforce 成不符合。"""
    from app.core.extractor.evidence_verdict import EVIDENCE_INSUFFICIENT_CONTENT

    pair = _make_pair(response=_make_response(content=EVIDENCE_INSUFFICIENT_CONTENT))
    outcome = _evaluate(pair)
    assert outcome.abstained is True
    assert det.ABSTAIN_EVIDENCE_INSUFFICIENT in outcome.abstain_reasons


def test_abstain_on_retrieval_starvation_flag() -> None:
    """检索饥饿: "没找到"可能只是没检索到，不能推出不存在。

 文案收口: 用户可见前缀改为"[建议人工复核]", 判定走
 is_starvation_note 单源 helper。
 """
    pair = _make_pair(
        response=_make_response(relevance_note="[建议人工复核] 投标文件第22,23,24页可能包含相关材料")
    )
    outcome = _evaluate(pair)
    assert outcome.abstained is True
    assert det.ABSTAIN_RETRIEVAL_STARVATION in outcome.abstain_reasons


def test_abstain_on_legacy_starvation_prefix_still_recognized() -> None:
    """存量数据库 relevance_note 仍为旧技术前缀, 弃权判定必须向后兼容。"""
    pair = _make_pair(response=_make_response(relevance_note="[检索饥饿] 未覆盖页码 22,23,24"))
    outcome = _evaluate(pair)
    assert outcome.abstained is True
    assert det.ABSTAIN_RETRIEVAL_STARVATION in outcome.abstain_reasons


def test_abstain_on_retrieval_starvation_from_quality_flags() -> None:
    """饥饿信号也可能只出现在证据包 quality_flags 上，同样必须弃权。"""
    bundle = _make_bundle(quality_flags=["[建议人工复核] 投标文件第5,6,7页可能包含相关材料"])
    outcome = _evaluate(_make_pair(), bundle)
    assert outcome.abstained is True
    assert det.ABSTAIN_RETRIEVAL_STARVATION in outcome.abstain_reasons


def test_abstain_on_missing_retrieval_evidence_flag() -> None:
    """证据装配降级 → 证据链不完备 → 弃权。"""
    bundle = _make_bundle(quality_flags=["missing_retrieval_evidence"])
    outcome = _evaluate(_make_pair(), bundle)
    assert outcome.abstained is True
    assert det.ABSTAIN_EVIDENCE_MISSING in outcome.abstain_reasons


def test_abstain_on_unknown_quality_flag() -> None:
    """未知降级信号必须默认按证据存疑处理（失效方向倒向弃权）。"""
    bundle = _make_bundle(quality_flags=["some_future_degradation_signal"])
    outcome = _evaluate(_make_pair(), bundle)
    assert outcome.abstained is True
    assert det.ABSTAIN_EVIDENCE_MISSING in outcome.abstain_reasons


def test_abstain_when_evidence_bundle_absent() -> None:
    """证据包缺失即输入不完备。"""
    outcome = evaluate_no_response_pair(_make_pair(), None, mode=MODE_SHADOW)
    assert outcome.abstained is True
    assert det.ABSTAIN_EVIDENCE_MISSING in outcome.abstain_reasons


def test_abstain_on_scoring_item() -> None:
    """评分项无条件参与 LLM 审查，算法不得代劳。"""
    outcome = _evaluate(_make_pair(requirement_kind="scoring_item"))
    assert outcome.abstained is True
    assert det.ABSTAIN_SCORING_ITEM in outcome.abstain_reasons


def test_abstain_on_key_review_item() -> None:
    """重点评审项同上。"""
    outcome = _evaluate(_make_pair(is_key_review=1))
    assert outcome.abstained is True
    assert det.ABSTAIN_KEY_REVIEW_ITEM in outcome.abstain_reasons


def test_abstain_on_supplementary_image_evidence() -> None:
    """已找到疑似扫描件材料时，"未响应"前提本身被动摇。"""
    bundle = _make_bundle(image_evidence="【图片附件】第18页 含公司证照扫描件")
    outcome = _evaluate(_make_pair(), bundle)
    assert outcome.abstained is True
    assert det.ABSTAIN_SUPPLEMENTARY_EVIDENCE in outcome.abstain_reasons


def test_abstain_on_cross_category_evidence() -> None:
    """跨类别证据同样构成"可能有响应"的信号。"""
    bundle = _make_bundle(cross_category_evidence="[跨类别证据·来自商务类响应] 相关业绩表述")
    outcome = _evaluate(_make_pair(), bundle)
    assert outcome.abstained is True
    assert det.ABSTAIN_SUPPLEMENTARY_EVIDENCE in outcome.abstain_reasons


def test_abstain_when_relevance_not_actionable() -> None:
    """上游判 procedural 时算法不得静默推翻为"不符合"。"""
    for relevance in ("procedural", "informational"):
        outcome = _evaluate(_make_pair(review_relevance=relevance))
        assert outcome.abstained is True, relevance
        assert det.ABSTAIN_RELEVANCE_NOT_ACTIONABLE in outcome.abstain_reasons


def test_abstain_on_pure_formal_requirement() -> None:
    """纯形式/程序要求属放行方向，归 PhysicalFormRequirementSkipGate，本模块弃权。"""
    outcome = _evaluate(_make_pair(description="投标文件正本一份、副本四份，须按装订要求装订成册。"))
    assert outcome.abstained is True
    assert det.ABSTAIN_FORMAL_REQUIREMENT in outcome.abstain_reasons


def test_abstain_on_requirement_text_too_short() -> None:
    """要求文本过短 = 输入不完备。"""
    outcome = _evaluate(_make_pair(description="须提供。"))
    assert outcome.abstained is True
    assert det.ABSTAIN_REQUIREMENT_TEXT_TOO_SHORT in outcome.abstain_reasons


# ---------------------------------------------------------------------------
# 三类真问题: 断言算法不放行
# ---------------------------------------------------------------------------


def test_bug868_compound_requirement_is_not_passed_through() -> None:
    """ 案例1: "必须使用指定报价表 + 装订要求"被整条按形式类放行。

 形式词与实质词同现即复合要求，按 教训须按最高实质性归类 → 弃权。
 """
    pair = _make_pair(
        description=(
            "投标报价必须使用招标文件附件二规定的报价一览表格式填写，"
            "并与投标文件一并装订要求成册后密封递交。"
        ),
    )
    outcome = _evaluate(pair)
    assert outcome.abstained is True
    assert det.ABSTAIN_COMPOUND_REQUIREMENT in outcome.abstain_reasons
    assert outcome.verdict is None, "复合要求下不得产出任何结论建议"


def test_bug868_scoring_rule_text_without_scoring_kind_is_not_passed_through() -> None:
    """ 案例2: 报价评分规则未被标为 scoring_item，仍按"评标程序"放行。

 requirement_kind 漏标时必须由文本中的评分语义兜底。
 """
    pair = _make_pair(
        description="报价得分按最低评标价法计算，基准价得满分，每高于基准价1%扣2分，扣完为止。",
        requirement_kind="normal",
        is_key_review=0,
    )
    outcome = _evaluate(pair)
    assert outcome.abstained is True
    assert det.ABSTAIN_SCORING_LANGUAGE in outcome.abstain_reasons


def test_bug868_insufficient_evidence_is_not_passed_through() -> None:
    """ 案例3: 证据关联不足仍给结论。任何证据存疑一律弃权。"""
    bundle = _make_bundle(quality_flags=["missing_retrieval_evidence"])
    outcome = _evaluate(_make_pair(is_mandatory=1), bundle)
    assert outcome.abstained is True
    assert outcome.verdict is None


def test_mandatory_veto_never_silently_passes() -> None:
    """否决项即使判定唯一也必须带 needs_review，且结论方向只能是"不符合"。"""
    outcome = _evaluate(_make_pair(is_mandatory=1))
    assert outcome.needs_review is True, "否决项算法结论必须进人工复核队列"
    assert outcome.verdict.suggested_patch["compliance_level"] == "not"
    assert outcome.verdict.suggested_patch["is_compliant"] is False


def test_module_never_suggests_a_passing_conclusion() -> None:
    """结构性护栏: 本模块在任何输入下都不得产出 fully/partially 建议。"""
    candidates = [
        _make_pair(),
        _make_pair(is_mandatory=1),
        _make_pair(description="投标文件须加盖公章并提供法定代表人授权委托书原件。"),
        _make_pair(review_relevance="procedural"),
        _make_pair(requirement_kind="scoring_item"),
    ]
    for pair in candidates:
        outcome = _evaluate(pair)
        if outcome.verdict is not None:
            level = outcome.verdict.suggested_patch["compliance_level"]
            assert level == "not", f"出现放行倾向建议: {level}"


# ---------------------------------------------------------------------------
# 三态模式
# ---------------------------------------------------------------------------


def test_mode_off_short_circuits_without_evaluation() -> None:
    """off: 直接弃权且不产出任何原因码（不计算即不观测）。"""
    outcome = _evaluate(_make_pair(), mode=MODE_OFF)
    assert outcome.abstained is True
    assert outcome.abstain_reasons == ()
    assert outcome.verdict is None
    assert build_enforced_result(_make_pair(), outcome) is None


def test_mode_shadow_computes_but_never_takes_over() -> None:
    """shadow: 算得出建议，但 build_enforced_result 必须返回 None。"""
    outcome = _evaluate(_make_pair(), mode=MODE_SHADOW)
    assert outcome.abstained is False
    assert outcome.verdict is not None
    assert build_enforced_result(_make_pair(), outcome) is None, (
        "shadow 模式绝不允许接管审查结论"
    )


def test_mode_enforce_takes_over_with_valid_result() -> None:
    """enforce: 产出合法 ExtractedReviewResult 并跳过 LLM。"""
    pair = _make_pair(is_mandatory=1)
    outcome = _evaluate(pair, mode=MODE_ENFORCE)
    result = build_enforced_result(pair, outcome)
    assert result is not None
    assert result.compliance_level == "not"
    assert result.risk_level == "high"
    assert result.is_compliant is False
    assert result.requirement_id == pair.requirement_id
    assert "人工复核" in result.comment, "否决项接管结论必须显式声明需人工复核"


def test_mode_enforce_still_respects_abstain() -> None:
    """enforce 下弃权项仍不得接管。"""
    outcome = _evaluate(_make_pair(requirement_kind="scoring_item"), mode=MODE_ENFORCE)
    assert build_enforced_result(_make_pair(), outcome) is None


def test_resolve_mode_defaults_to_shadow_on_invalid_value(monkeypatch) -> None:
    """非法配置值必须退回 shadow（失效方向 = 不生效）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "REVIEW_DETERMINISTIC_NO_RESPONSE_MODE", "ENFORCE!!", raising=False)
    assert resolve_mode() == MODE_SHADOW
    monkeypatch.setattr(settings, "REVIEW_DETERMINISTIC_NO_RESPONSE_MODE", "enforce", raising=False)
    assert resolve_mode() == MODE_ENFORCE


def test_default_mode_is_shadow() -> None:
    """默认配置必须是 shadow —— enforce 需 8b 人工标注验收后才可放行。"""
    from app.core.config import Settings

    assert Settings.model_fields["REVIEW_DETERMINISTIC_NO_RESPONSE_MODE"].default == MODE_SHADOW


# ---------------------------------------------------------------------------
# shadow 不改变实际审查结论（接线级）
# ---------------------------------------------------------------------------


class _RecordingLLM:
    """记录调用次数与 prompt 的 _call_llm_review 替身。"""

    def __init__(self, result):
        self.result = result
        self.calls: list[str] = []

    async def __call__(self, prompt, **kwargs):
        self.calls.append(prompt)
        return self.result


def _run_single_pair(monkeypatch, mode: str):
    """以 mode 运行 review_single_pair，返回 (结果对象, LLM 替身)。"""
    from app.core.ai.reviewer import ReviewOrchestrator
    from app.core.config import settings
    from app.schemas.review import ExtractedReviewResult

    monkeypatch.setattr(settings, "REVIEW_DETERMINISTIC_NO_RESPONSE_MODE", mode, raising=False)

    extracted = ExtractedReviewResult(
        requirement_id=101,
        bid_response_id=201,
        category="qualification",
        requirement_summary="业绩证明材料",
        response_summary="未找到响应",
        is_compliant=False,
        compliance_level="missing",
        score=10,
        comment="投标文件中未见对应业绩证明材料。",
        risk_level="medium",
    )
    llm = _RecordingLLM(extracted)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    review_result = SimpleNamespace(
        compliance_level="missing", consistency_flag=None, reconcile_note=None,
        score=10, risk_level="medium",
    )

    async def _create(project_id, pair, extracted_arg, session=None):
        return review_result

    async def _noop(*args, **kwargs):
        return None

    async def _no_skip(project_id, pair):
        return None

    monkeypatch.setattr("app.core.database.async_session_factory", _Session, raising=False)

    orch = object.__new__(ReviewOrchestrator)
    orch.item_timeout = 30
    orch._call_llm_review = llm
    orch._try_pre_llm_skip = _no_skip
    orch._create_review_result = _create
    orch._maybe_run_cove_investigate = _noop
    orch._maybe_run_judge_investigate = _noop
    orch._build_no_response_prompt = lambda *a, **kw: "NO_RESPONSE_PROMPT_BODY"
    orch._build_review_prompt = lambda *a, **kw: "REVIEW_PROMPT_BODY"

    from app.core.ai.reviewer_parts.pair_review import review_single_pair

    pair = _make_pair()
    result = asyncio.run(review_single_pair(orch, 7, pair, _make_bundle()))
    return result, llm


def test_shadow_mode_leaves_llm_path_byte_identical(monkeypatch) -> None:
    """shadow 与 off 两种模式下，LLM 被调用的 prompt 与最终结果必须逐字段一致。"""
    off_result, off_llm = _run_single_pair(monkeypatch, MODE_OFF)
    shadow_result, shadow_llm = _run_single_pair(monkeypatch, MODE_SHADOW)

    assert off_llm.calls == shadow_llm.calls == ["NO_RESPONSE_PROMPT_BODY"], (
        "shadow 不得改变 LLM 调用次数或 prompt 内容"
    )
    for field_name in ("compliance_level", "risk_level", "score", "consistency_flag"):
        assert getattr(off_result, field_name) == getattr(shadow_result, field_name), (
            f"shadow 改变了字段 {field_name}"
        )


def test_enforce_mode_skips_llm_call(monkeypatch) -> None:
    """enforce: 未弃权时 LLM 不应被调用（与 shadow 形成对照）。"""
    _, llm = _run_single_pair(monkeypatch, MODE_ENFORCE)
    assert llm.calls == [], "enforce 命中时不应再调用 LLM"


def test_evaluation_failure_never_breaks_review(monkeypatch) -> None:
    """判定模块抛异常时主审查流程必须继续走 LLM（fail-safe）。"""
    def _boom(*args, **kwargs):
        raise RuntimeError("deliberate failure")

    monkeypatch.setattr(det, "evaluate_no_response_pair", _boom)
    result, llm = _run_single_pair(monkeypatch, MODE_SHADOW)
    assert llm.calls == ["NO_RESPONSE_PROMPT_BODY"]
    assert result is not None


# ---------------------------------------------------------------------------
# 影子采集本身不得影响主流程
# ---------------------------------------------------------------------------


def test_record_shadow_comparison_is_fail_safe() -> None:
    """采集函数对任何畸形输入都必须静默吞掉异常。"""
    broken = DeterministicNoResponseOutcome(
        mode=MODE_SHADOW, abstained=False, verdict=None,
    )
    det.record_shadow_comparison(_make_pair(), broken, None)
    det.record_shadow_comparison(None, broken, object())


def test_record_shadow_comparison_counts_agreement_dimensions() -> None:
    """一致率至少覆盖 compliance_level 与 risk_level 两个维度。"""
    from app.core.observability.metrics import (
        deterministic_no_response_agreement_total,
        deterministic_no_response_disagreement_total,
    )

    pair = _make_pair(is_mandatory=1)
    outcome = _evaluate(pair)

    def _sample_sum(counter) -> float:
        return sum(
            sample.value
            for metric in counter.collect()
            for sample in metric.samples
            if sample.name.endswith("_total")
        )

    before_agree = _sample_sum(deterministic_no_response_agreement_total)
    before_disagree = _sample_sum(deterministic_no_response_disagreement_total)

    # 算法 not/high vs LLM not/high -> 两维均一致
    det.record_shadow_comparison(
        pair, outcome, SimpleNamespace(compliance_level="not", risk_level="high"),
    )
    assert _sample_sum(deterministic_no_response_agreement_total) == before_agree + 2

    # 算法 not/high vs LLM fully/none -> 两维均分歧
    det.record_shadow_comparison(
        pair, outcome, SimpleNamespace(compliance_level="fully", risk_level="none"),
    )
    assert _sample_sum(deterministic_no_response_disagreement_total) == before_disagree + 2


def test_abstain_reasons_are_deduplicated_and_ordered() -> None:
    """多条件同时命中时原因码去重且保持稳定顺序（供指标 label 使用）。"""
    pair = _make_pair(
        description="投标文件须装订要求成册，并提供资质证书与业绩合同复印件。",
        requirement_kind="scoring_item",
        is_key_review=1,
        review_relevance="procedural",
    )
    outcome = _evaluate(pair, _make_bundle(quality_flags=["missing_retrieval_evidence"]))
    assert outcome.abstained is True
    assert len(outcome.abstain_reasons) == len(set(outcome.abstain_reasons))
    assert outcome.primary_abstain_reason == outcome.abstain_reasons[0]
    for expected in (
        det.ABSTAIN_SCORING_ITEM,
        det.ABSTAIN_KEY_REVIEW_ITEM,
        det.ABSTAIN_RELEVANCE_NOT_ACTIONABLE,
        det.ABSTAIN_COMPOUND_REQUIREMENT,
        det.ABSTAIN_EVIDENCE_MISSING,
    ):
        assert expected in outcome.abstain_reasons

"""EvidenceGate 基础设施（Layer 5）。

提供 Gate 抽象基类、GatePipeline 执行器、GateRegistry 注册表。
签名严格按 docs/evidence_gate_design.md §3 定义。

B3 新增：
- build_pipeline_for(flow)  工厂函数
- should_use_gate_pipeline(flow, project_id)  灰度判定
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from .base import Gate, GateContext, GateVerdict
from .pipeline import GatePipeline
from .registry import GateRegistry

if TYPE_CHECKING:
    from .base import Flow

__all__ = [
    "Gate",
    "GateContext",
    "GateVerdict",
    "GatePipeline",
    "GateRegistry",
    "build_pipeline_for",
    "should_use_gate_pipeline",
    "should_use_algorithm_first",
]


def _get_all_gates() -> list[Gate]:
    """Lazily import and instantiate all registered Gate subclasses."""
    from .collusion.cross_dimension_hint import CrossDimensionHintGate
    from .collusion.d1_personnel_claim import D1PersonnelClaimGate
    from .collusion.d2_company_claim import D2CompanyClaimGate
    from .collusion.d3_text_algorithmic import D3TextAlgorithmicGate
    from .collusion.d5_business_relation import D5BusinessRelationGate
    from .collusion.d5_qual_type_exemption import D5QualTypeExemptionGate
    from .collusion.d6_format_algorithmic import D6FormatAlgorithmicGate
    from .collusion.d7_abnormal_consistency import D7AbnormalConsistencyGate
    from .collusion.d7_concrete_evidence import D7ConcreteEvidenceGate
    from .collusion.finding_page_quote_verify import FindingPageQuoteVerifyGate
    from .collusion.paired_quote_identity import PairedQuoteIdentityGate
    from .review.algorithmic_risk_band import AlgorithmicRiskBandGate
    from .review.claim_response_consistency import ClaimResponseConsistencyGate
    from .review.cross_group_conflict import CrossGroupConflictGate
    from .review.ledger_presence import LedgerPresenceGate
    from .review.page_anchor_verify import PageAnchorVerifyGate
    from .review.physical_form_skip import PhysicalFormRequirementSkipGate
    from .review.prohibition_leak import ProhibitionLeakGate
    from .review.relevance_anomaly_backfill import RelevanceAnomalyBackfillGate
    from .review.relevance_guard import RelevanceGuardGate
    from .review.scoring_presence_max import ScoringPresenceMaxGate
    from .review.scoring_quote_presence import ScoringQuotePresenceGate
    from .writer.cross_reference import CrossReferenceGate
    from .writer.de_ai_filter import DeAIFilterGate
    from .writer.disqualification_risk import DisqualificationRiskGate
    from .writer.fixed_template_placeholder import FixedTemplatePlaceholderGate
    from .writer.material_source_trace import MaterialSourceTraceGate
    from .writer.placeholder_density import PlaceholderDensityGate
    from .writer.required_section_completeness import RequiredSectionCompletenessGate
    from .writer.table_field_fill_rate import TableFieldFillRateGate
    from .writer.terminology_consistency import TerminologyConsistencyGate

    return [
        # investigate pre_llm (B4)
        PhysicalFormRequirementSkipGate(),
        # investigate post_llm (order asc)
        PageAnchorVerifyGate(),
        RelevanceGuardGate(),
        ClaimResponseConsistencyGate(),
        LedgerPresenceGate(),
        ScoringQuotePresenceGate(),
        ProhibitionLeakGate(),
        ScoringPresenceMaxGate(),
        AlgorithmicRiskBandGate(),
        # investigate post_pipeline
        CrossGroupConflictGate(),
        RelevanceAnomalyBackfillGate(),
        # collusion post_llm
        D1PersonnelClaimGate(),
        D2CompanyClaimGate(),
        D5BusinessRelationGate(),
        D5QualTypeExemptionGate(),
        FindingPageQuoteVerifyGate(),  # order=50: 页码-引文联合校验（B1 重写）
        PairedQuoteIdentityGate(),  # order=51: D1/D2 成对引文分侧主体锚定
        D7ConcreteEvidenceGate(),  # order=52: D6/D7 具体证据校验（C3）
        D3TextAlgorithmicGate(),  # S.5.3: D3 纯算法对跑（advisory）
        D6FormatAlgorithmicGate(),
        D7AbnormalConsistencyGate(),
        # collusion post_pipeline
        CrossDimensionHintGate(),
        # writer post_llm (B5 + S.5.2)
        FixedTemplatePlaceholderGate(),
        MaterialSourceTraceGate(),
        DeAIFilterGate(),
        TableFieldFillRateGate(),  # S.5.2: data_fill 表格填充率
        # writer post_pipeline (B5)
        DisqualificationRiskGate(),
        RequiredSectionCompletenessGate(),
        CrossReferenceGate(),
        TerminologyConsistencyGate(),
        PlaceholderDensityGate(),
    ]


def build_pipeline_for(flow: Flow, registry: GateRegistry | None = None) -> GatePipeline:
    """Build a GatePipeline containing only Gates matching *flow*."""
    if registry is None:
        from app.core.config import settings
        registry = GateRegistry(global_overrides=settings.GATE_SEVERITY_OVERRIDES)

    gates = [g for g in _get_all_gates() if g.flow == flow or g.flow == "all"]
    for g in gates:
        registry.register(g)
    return GatePipeline(gates, registry=registry)


def should_use_algorithm_first(project_id: int | str | None) -> bool:
    """Determine whether to use algorithm-first mode for investigate flow.

    Two-layer switch: ``INVESTIGATE_ALGORITHM_FIRST_ENABLED`` (master)
    + ``INVESTIGATE_ALGORITHM_FIRST_RATIO`` (gradual rollout).
    Hash uses the same md5 approach as ``should_use_gate_pipeline``.
    """
    from app.core.config import settings

    if not settings.INVESTIGATE_ALGORITHM_FIRST_ENABLED:
        return False
    ratio = settings.INVESTIGATE_ALGORITHM_FIRST_RATIO
    if ratio <= 0.0 or project_id is None:
        return False
    if ratio >= 1.0:
        return True
    h = int(hashlib.md5(str(project_id).encode()).hexdigest(), 16) % 100
    return h < int(ratio * 100)


_REVIEW_RATIO_WARN_EMITTED = False


def should_use_gate_pipeline(flow: str, project_id: int | str | None) -> bool:
    """Determine whether to use Gate Pipeline (vs legacy) for this request.

 Uses the same hash-based approach as B2 constrained decoding:
 ``hash(project_id) % 100 < ratio * 100``.

 Fix-01 (review/investigate)：reviewer.py 的 Gate Pipeline 已改为无条件执行，
 本函数对 ``review``/``investigate`` flow 的返回值**不再影响**投标审查主链路。
 (2026-04-24)：``collusion`` 流 legacy 双路已彻底下线，此处对 collusion
 恒返 True，``GATE_PIPELINE_RATIO_COLLUSION`` 配置项已移除。``writer`` 流仍
 保留灰度能力。若检测到 ``GATE_PIPELINE_RATIO_REVIEW`` 非 1.0 配置，启动期打
 一次 WARNING 提示：紧急回滚请使用 ``GATE_SEVERITY_OVERRIDES``。
 """
    global _REVIEW_RATIO_WARN_EMITTED
    from app.core.config import settings

    # collusion 流无条件执行 Pipeline，不再读取 ratio 配置
    if flow == "collusion":
        return True

    ratio_map = {
        "investigate": settings.GATE_PIPELINE_RATIO_REVIEW,
        "review": settings.GATE_PIPELINE_RATIO_REVIEW,
        "writer": settings.WRITER_GATE_PIPELINE_RATIO,
    }
    ratio = ratio_map.get(flow, 0.0)

    if flow in ("review", "investigate") and ratio < 1.0 and not _REVIEW_RATIO_WARN_EMITTED:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "GATE_PIPELINE_RATIO_REVIEW=%s 已被 Fix-01 忽略: review/investigate 流 Gate Pipeline 无条件执行, "
            "如需紧急关闭单个 Gate 请配置 GATE_SEVERITY_OVERRIDES='{\"GateName\":\"off\"}'",
            ratio,
        )
        _REVIEW_RATIO_WARN_EMITTED = True

    if ratio <= 0.0:
        return False
    if ratio >= 1.0:
        return True

    if project_id is None:
        return False
    h = int(hashlib.md5(str(project_id).encode()).hexdigest(), 16) % 100
    return h < int(ratio * 100)

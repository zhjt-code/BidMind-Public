"""审查结果构造（R2-3 Step 4 抽出）

涵盖以下原 `ReviewOrchestrator` 方法：

- 成功结果构造: `create_review_result`（120 行，含 Gate Pipeline 驱动 + 风险带 enforce）
- 失败兜底: `create_failure_result` / `create_failure_result_safe`（13 字段失败合同）
- 算法证据（@staticmethod）: `compute_algorithm_evidence`（B4 算法工具结果）
- 算法风险带 enforce（@staticmethod）: `maybe_enforce_algorithmic_risk_band`（S.5.1）
- 文本工具: `summarize_text`

外部测试侧已知调用路径:
- `ReviewOrchestrator._compute_algorithm_evidence(pair)`（类路径静态调用）
- `ReviewOrchestrator._maybe_enforce_algorithmic_risk_band(extracted, verdicts)`（类路径）
→ 主类必须保留 `@staticmethod` thin forwarder。

签名规范: 子模块函数去前导下划线; staticmethod 不接收 self,普通方法保留 self。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

from app.core.matcher.requirement_matcher import MatchedPair
from app.core.review_contracts import parse_json_list, parse_page_numbers
from app.models.review_result import ComplianceLevel, ReviewResult, RiskLevel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.ai.reviewer import ReviewOrchestrator
    from app.schemas.review import ExtractedReviewResult


# ---------------------------------------------------------------------------
# 失败兜底标记（生产者与识别者同源，避免两处口径漂移）
# ---------------------------------------------------------------------------

# 兜底结果的 comment 标记；下游据此识别"这条不是有效审查结论"。
FAILURE_COMMENT_MARKER = "LLM_REVIEW_FAILURE"


def is_failure_result(result: object) -> bool:
    """结果是否为审查失败兜底（而非有效审查结论）。: 兜底结果是非 None 的 ReviewResult, 任何以"非 None"判定成功的下游
 (如 LLMRetryTracker 默认谓词)都会把失败误判为成功。本判据与上方两处
 create_failure_result 的 comment 标记同源, 修改标记必须同步此函数。
 """
    if result is None:
        return True
    return FAILURE_COMMENT_MARKER in (getattr(result, "comment", None) or "")


# ---------------------------------------------------------------------------
# 文本截断
# ---------------------------------------------------------------------------


def summarize_text(self: "ReviewOrchestrator", text: str, max_len: int = 200) -> str:
    """安全截断文本，避免过长。"""
    if not text:
        return ""
    return text if len(text) <= max_len else f"{text[:max_len]}..."


def _resolve_estimated_score(
    extracted: "ExtractedReviewResult",
    pair: MatchedPair,
    compliance_level: ComplianceLevel,
) -> Optional[float]:
    """计算评分项预估得分(P2 闭环, 评分项 scoring_item 或重点评审 is_key_review)。

 职责: 委托统一口径 compute_estimated_score(落库/读取/汇总共用), clamp 到 [0,满分];
 不符合/缺失响应记 0; 非评分项且非重点项返回 None。
 不做: 不改 compliance/score 既有判定; 预估仅用于展示与汇总。

 复发修正: 旧实现仅处理 scoring_item, 但 Prompt(_scoring_supplement)与读取侧
 兜底均覆盖 is_key_review, 落库环却把 is_key_review 挡在门外 -> estimated_score 恒 NULL,
 全部压力转嫁给事后正则。此处对齐口径并收口到单一真源。
 """
    req = pair.requirement if pair else None
    if req is None:
        return None
    applies = (
        getattr(req, "requirement_kind", "normal") == "scoring_item"
        or bool(getattr(req, "is_key_review", 0))
    )
    from app.core.review_contracts import compute_estimated_score
    return compute_estimated_score(
        structured_value=getattr(extracted, "estimated_score", None),
        comment=getattr(extracted, "comment", ""),
        max_score=getattr(req, "score_weight", None),
        compliance_level=compliance_level,
        applies=applies,
    )


# ---------------------------------------------------------------------------
# B4 算法证据（staticmethod, 外部测试类路径调用入口）
# ---------------------------------------------------------------------------


def compute_algorithm_evidence(pair: MatchedPair) -> dict:
    """B4: 计算算法工具结果，注入 Gate evidence 供 AlgorithmicRiskBandGate 使用。"""
    result: dict = {}
    req_desc = pair.requirement.description if pair.requirement else ""
    resp_content = (pair.response.content or "") if pair.response else ""
    try:
        from datetime import date

        from app.core.extractor.tools.date_range_checker import check_performance_anchor
        result["date_verdict"] = check_performance_anchor(
            req_desc, resp_content, date.today(),
        )
    except Exception:
        result["date_verdict"] = None
    try:
        # 修正: amount_verdict 必须为 AmountVerdict | None (契约见
        # app.core.ai.risk_band._amount_parseable/_amount_low_confidence)。
        # 历史实现把 compare_amount() 的字符串结果塞进来, 导致下游
        # derive_risk_band 访问 .confidence 时抛 AttributeError, 审查全量失败。
        #
        # 语义: 只有"招标要求本身含可解析金额"时才视为金额维度适用;
        # 适用时传入"响应侧的 AmountVerdict", 供风险带基于
        # canonical_value / confidence 做 unparseable / low-confidence 判定。
        # 额外用 amount_relation 键保存 compare_amount 的比较结果, 供审计使用。
        from app.core.extractor.tools.amount_parser import (
            compare_amount,
            parse_amount,
        )
        req_amount = parse_amount(req_desc) if req_desc else None
        if req_amount is not None and req_amount.canonical_value is not None:
            result["amount_verdict"] = parse_amount(resp_content) if resp_content else None
            result["amount_relation"] = compare_amount(req_desc, resp_content)
        else:
            result["amount_verdict"] = None
            result["amount_relation"] = None
    except Exception:
        result["amount_verdict"] = None
        result["amount_relation"] = None
    try:
        from app.core.matcher.relevance_scorer import score_relevance
        result["relevance_result"] = score_relevance(
            req_desc, resp_content, pair.category,
        )
    except Exception:
        result["relevance_result"] = None
    return result


# ---------------------------------------------------------------------------
# S.5.1 算法风险带条件升 enforce（staticmethod, 外部测试类路径调用入口）
# ---------------------------------------------------------------------------


def maybe_enforce_algorithmic_risk_band(
    extracted: "ExtractedReviewResult",
    verdicts: list,
) -> None:
    """S.5.1 · 高置信度下手动应用 AlgorithmicRiskBandGate 的 patch。

    Gate 默认 advisory，``Pipeline._apply_patches`` 对 advisory verdict 直接跳过；
    对于 ``rb.confidence >= settings.ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE``
    的分歧（verdict="warn"，suggested_patch 含 risk_level/failure_state），
    reviewer 在这里手动把 patch 应用到 extracted 上，实现"条件升 enforce"。

    选择在 reviewer 层而非动 Gate 架构的理由见 handover D-032。
    """
    if not verdicts:
        return
    try:
        from app.core.config import settings
        threshold = float(getattr(
            settings, "ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE", 0.8,
        ))
    except Exception:
        threshold = 0.8

    for v in verdicts:
        gate_name = getattr(v, "gate_name", "")
        if gate_name != "AlgorithmicRiskBandGate":
            continue
        verdict_kind = getattr(v, "verdict", "")
        if verdict_kind != "warn":
            continue
        confidence = float(getattr(v, "confidence", 0.0) or 0.0)
        if confidence < threshold:
            continue
        patch = getattr(v, "suggested_patch", None) or {}
        new_risk = patch.get("risk_level")
        if new_risk and hasattr(extracted, "risk_level"):
            extracted.risk_level = new_risk
        new_failure_state = patch.get("failure_state")
        if new_failure_state and hasattr(extracted, "failure_state"):
            extracted.failure_state = new_failure_state
        # 采集一次指标（升 enforce 实际触发）
        try:
            from app.core.observability.metrics import (  # noqa: PLC0415
                algorithmic_risk_band_disagreement_total,
            )
            algorithmic_risk_band_disagreement_total.labels(
                flow="investigate",
                alg_band=str(new_risk or "unknown"),
                llm_band="enforced_by_confidence",
                is_mandatory="unknown",
            ).inc()
        except Exception:
            pass
        logger.info(
            f"S.5.1 AlgorithmicRiskBandGate enforced: "
            f"risk_level→{new_risk}, confidence={confidence:.3f}"
        )
        break  # 只有一个 AlgorithmicRiskBandGate verdict，应用一次即可


# ---------------------------------------------------------------------------
# 成功审查结果构造（Gate Pipeline 驱动）
# ---------------------------------------------------------------------------


def _base_review_gate_evidence(pair: MatchedPair) -> dict:
    """组装 post_llm Gate 的基础证据（含评分对象字段）。: 补充 requirement_text / completeness / missing_elements /
 evidence_anchor / source_pages，供一致性类 Gate 对照要求原文与响应元数据。
 """
    from app.core.ai.gates.review.scoring_object_detect import (
        build_scoring_object_evidence,
    )

    resp = pair.response
    evidence: dict = {
        "is_mandatory": pair.is_mandatory,
        "has_response": pair.has_response,
        "response_content": (resp.content or "").strip() if resp else "",
        "category": pair.category,
        "requirement_text": (
            pair.requirement.description if pair.requirement else ""
        ),
        "completeness": getattr(resp, "completeness", None) if resp else None,
        "missing_elements": parse_json_list(
            getattr(resp, "missing_elements", None) if resp else None
        ),
        "evidence_anchor": getattr(resp, "evidence_anchor", None) if resp else None,
        "source_pages": parse_page_numbers(
            getattr(resp, "source_pages", None) if resp else None
        ),
        "evidence_pack_text": (
            getattr(pair, "_gate_evidence_pack_text", "") or ""
        ).strip(),
        "prohibition_leak_hits": list(
            getattr(pair, "_gate_prohibition_leak_hits", None)
            or getattr(pair, "_gate_price_leak_hits", None)
            or ()
        ),
        "ledger_atoms": list(getattr(pair, "_gate_ledger_atoms", None) or ()),
        "facet_states": list(
            getattr(resp, "facet_states", None) or ()
        ) if resp else [],
        "sub_requirements": parse_json_list(
            getattr(pair.requirement, "sub_requirements", None) if pair.requirement else None
        ),
        "keywords": parse_json_list(
            getattr(pair.requirement, "keywords", None) if pair.requirement else None
        ),
    }
    evidence.update(build_scoring_object_evidence(pair))
    return evidence


def _finalize_scoring_fields(
    extracted: "ExtractedReviewResult",
    pair: MatchedPair,
    compliance_level: ComplianceLevel,
) -> tuple[Optional[list], Optional[float], Optional[str]]:
    """归一化逐规则判定并收敛预估分（取较低）。"""
    from app.core.ai.reviewer_parts import scoring_rubric as _sr

    req_meta = (
        getattr(pair.requirement, "scoring_metadata", None) or {}
        if pair.requirement else {}
    )
    max_score = req_meta.get("max_score") or getattr(
        pair.requirement, "score_weight", None,
    )
    assessments = _sr.normalize_rule_assessments(
        getattr(extracted, "rule_assessments", None),
        max_score=max_score,
    )
    estimated, rubric_note = _sr.reconcile_estimated_score(
        _resolve_estimated_score(extracted, pair, compliance_level),
        assessments,
        max_score=max_score,
    )
    reconcile_note = extracted.reconcile_note
    if rubric_note:
        reconcile_note = (
            f"{reconcile_note}; {rubric_note}" if reconcile_note else rubric_note
        )
    return assessments, estimated, reconcile_note


async def create_review_result(
    self: "ReviewOrchestrator",
    project_id: int,
    pair: MatchedPair,
    extracted: "ExtractedReviewResult",
    session: Optional["AsyncSession"] = None,
) -> ReviewResult:
    """创建审查结果对象

 Fix-01: review 流 Gate Pipeline 为唯一主路径，不再保留 inline fallback。
 紧急关闭任一 Gate 走 ``GATE_SEVERITY_OVERRIDES`` env 覆盖。: 新增 session 参数透传到 _collect_chunk_pages_for_pair,
 并发协程使用短 session 而非共享 self.db, 避免连接池耗尽。
 """
    from app.core.ai.gates import build_pipeline_for
    from app.core.ai.gates.base import GateContext
    from app.core.ai.reviewer import COMPLIANCE_LEVEL_MAP, RISK_LEVEL_MAP

    _evidence: dict = _base_review_gate_evidence(pair)
    try:
        _evidence.update(self._compute_algorithm_evidence(pair))
    except Exception as _b4_err:
        logger.debug(f"B4 algorithm evidence 降级: {_b4_err}")
    # S.2.4: 页码锚定证据注入 —— source_pages + chunk_pages 供
    # PageAnchorVerifyGate 做范围校验
    try:
        _source_pages = parse_page_numbers(
            pair.response.source_pages if pair.response else None
        )
        _chunk_pages = await self._collect_chunk_pages_for_pair(pair, session=session)
        if _source_pages:
            _evidence["source_pages"] = _source_pages
        if _chunk_pages:
            _evidence["chunk_pages"] = _chunk_pages
    except Exception as _anchor_err:
        logger.debug(f"S.2.4 page anchor evidence 降级: {_anchor_err}")
    _ctx = GateContext(
        flow="investigate", stage="post_llm",
        target=extracted, evidence=_evidence,
        config={"project_id": project_id},
    )
    _pipeline = build_pipeline_for("investigate")
    _verdicts = _pipeline.run(_ctx, apply_patch_to=extracted)
    # S.5.1: AlgorithmicRiskBandGate 条件升 enforce（绕 Pipeline advisory skip）
    # Gate 默认 advisory 时 Pipeline._apply_patches 跳过其 patch；但高置信度
    # 分歧（rb.confidence >= ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE）下
    # 算法判定相比 LLM 更可信，这里按条件手动应用 patch。
    # 条件未满足 → 仅保留 advisory 语义（comment 侧由 Gate trace 记录，不改结论）
    self._maybe_enforce_algorithmic_risk_band(extracted, _verdicts)

    existence_failure_state = None
    try:
        from app.core.ai.reviewer_parts.existence_finalizer import (
            reconcile_existence_final_state,
        )
        extracted, existence_failure_state = reconcile_existence_final_state(
            extracted, pair, _evidence
        )
    except Exception as _exist_err:
        logger.debug(f"存在性终态重算降级: {_exist_err}")

    # B3: PageAnchorVerifyGate 给出未通过页码时，把警示拼到 comment 末尾，
    # 让用户在 UI 看到"原文位置未通过自动核验"。Pipeline._apply_patches 不做
    # 字符串追加，因此这里手动处理。幂等：suffix 只追加一次。
    for v in _verdicts:
        if v.gate_name != "PageAnchorVerifyGate":
            continue
        patch = v.suggested_patch or {}
        suffix = patch.get("_comment_suffix")
        if not suffix:
            continue
        existing = (extracted.comment or "")
        if suffix.strip() and suffix.strip() not in existing:
            extracted.comment = existing + suffix

    compliance_level = COMPLIANCE_LEVEL_MAP.get(
        extracted.compliance_level,
        ComplianceLevel.NOT
    )
    risk_level = RISK_LEVEL_MAP.get(
        extracted.risk_level,
        RiskLevel.MEDIUM
    )

    _norm_assessments, _estimated_score, _reconcile_note = _finalize_scoring_fields(
        extracted, pair, compliance_level,
    )

    # 落库入口闸门——comment/摘要在入库前过用户态清洗，
    # 杜绝 is_mandatory=true / technical页 / relevance_score<30 等内部术语落库；
    # 读取侧 sanitize 保留为第二道兜底（幂等）。
    from app.core.i18n import sanitize_user_facing_text as _sx

    return ReviewResult(
        project_id=project_id,
        requirement_id=pair.requirement_id,
        bid_response_id=pair.response_id,
        requirement_summary=_sx(extracted.requirement_summary),
        response_summary=_sx(extracted.response_summary),
        is_compliant=1 if extracted.is_compliant else 0,
        compliance_level=compliance_level,
        score=extracted.score,
        estimated_score=_estimated_score,
        comment=_sx(extracted.comment),
        risk_level=risk_level,
        canonical_key=(
            extracted.canonical_key
            or (pair.requirement.canonical_key if pair.requirement else None)
        ),
        fact_key=(
            extracted.fact_key
            or extracted.canonical_key
            or (pair.requirement.canonical_key if pair.requirement else None)
        ),
        family_type=(
            extracted.family_type
            or self._detect_family_type(
                pair.requirement.description if pair.requirement else "",
                pair.category,
            )
        ),
        attribution_type=(
            extracted.attribution_type
            or self._derive_attribution_type(pair.requirement)
        ),
        evidence_relation=extracted.evidence_relation or "undetermined",
        consistency_flag=extracted.consistency_flag or "unchecked",
        reconcile_note=_sx(_reconcile_note) or None,
        main_requirement_id=(
            extracted.main_requirement_id
            or (pair.requirement.main_requirement_id if pair.requirement else None)
        ),
        relevance_score=extracted.relevance_score,
        relevance_note=extracted.relevance_note,
        rule_assessments=_norm_assessments,
        failure_state=(
            existence_failure_state
            or getattr(extracted, "failure_state", None)
            or "completed"
        ),
    )


# ---------------------------------------------------------------------------
# 失败兜底（13 字段失败合同）
# ---------------------------------------------------------------------------


def create_failure_result(
    self: "ReviewOrchestrator",
    project_id: int,
    pair: MatchedPair,
) -> ReviewResult:
    """创建审查失败的兜底结果，避免流程卡住(非并发场景)。"""
    requirement_summary = self._summarize_text(pair.requirement.description)
    response_summary = None
    if pair.response and pair.response.content:
        response_summary = self._summarize_text(pair.response.content)

    risk_level = RiskLevel.HIGH if pair.is_mandatory else RiskLevel.MEDIUM

    return ReviewResult(
        project_id=project_id,
        requirement_id=pair.requirement_id,
        bid_response_id=pair.response_id,
        requirement_summary=requirement_summary,
        response_summary=response_summary,
        is_compliant=0,
        compliance_level=ComplianceLevel.MISSING,
        score=0,
        comment=f"审查失败[{FAILURE_COMMENT_MARKER}]：模型调用超时或输出不合规，请人工复核",
        risk_level=risk_level,
        canonical_key=pair.requirement.canonical_key if pair.requirement else None,
        fact_key=pair.requirement.canonical_key if pair.requirement else None,
        family_type=self._detect_family_type(
            pair.requirement.description if pair.requirement else "",
            pair.category,
        ),
        attribution_type=self._derive_attribution_type(pair.requirement),
        evidence_relation="undetermined",
        consistency_flag="unchecked",
        reconcile_note="自动兜底结果，待复核",
        main_requirement_id=(
            pair.requirement.main_requirement_id if pair.requirement else None
        ),
    )


def create_failure_result_safe(
    self: "ReviewOrchestrator",
    project_id: int,
    requirement_id: int,
    response_id: Optional[int],
    pair: MatchedPair,
) -> ReviewResult:
    """创建审查失败的兜底结果(并发安全版)

    接收预取的标量ID,对ORM属性访问用try-except保护,
    确保不会因DetachedInstanceError二次抛异常。
    """
    try:
        requirement_summary = self._summarize_text(pair.requirement.description)
    except Exception:
        requirement_summary = ""
    try:
        response_summary = None
        if pair.response and pair.response.content:
            response_summary = self._summarize_text(pair.response.content)
    except Exception:
        response_summary = None
    try:
        is_mandatory = pair.is_mandatory
    except Exception:
        is_mandatory = True

    risk_level = RiskLevel.HIGH if is_mandatory else RiskLevel.MEDIUM

    return ReviewResult(
        project_id=project_id,
        requirement_id=requirement_id,
        bid_response_id=response_id,
        requirement_summary=requirement_summary,
        response_summary=response_summary,
        is_compliant=0,
        compliance_level=ComplianceLevel.MISSING,
        score=0,
        comment=f"审查失败[{FAILURE_COMMENT_MARKER}]：模型调用超时或输出不合规，请人工复核",
        risk_level=risk_level,
        canonical_key=pair.requirement.canonical_key if pair.requirement else None,
        fact_key=pair.requirement.canonical_key if pair.requirement else None,
        family_type=self._detect_family_type(
            pair.requirement.description if pair.requirement else "",
            pair.category,
        ),
        attribution_type=self._derive_attribution_type(pair.requirement),
        evidence_relation="undetermined",
        consistency_flag="unchecked",
        reconcile_note="自动兜底结果，待复核",
        main_requirement_id=(
            pair.requirement.main_requirement_id if pair.requirement else None
        ),
    )

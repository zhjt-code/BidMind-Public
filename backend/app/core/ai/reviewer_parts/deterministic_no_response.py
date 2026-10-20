"""阶段8a 未响应确定性判定（off / shadow / enforce 三态，默认 shadow）。

职责: 在"投标未响应"且证据完备、判定唯一时给出"不符合"建议与可审计的判定证明。
不做: 不处理有响应场景; 不给出"符合/放行"建议(放行归 PhysicalFormRequirementSkipGate);
 不处理评分项/重点评审项; 不处理证据降级、检索饥饿或存在补充证据的场景;
 不读写 DB、不调 LLM、不改写审查结论(shadow 下仅记录对比)。

为什么只做"不符合"方向: 放行方向已由 B4 PhysicalFormRequirementSkipGate 承担并在
阶段1 依 加固过(必审要求禁止算法放行)。若本模块另造一套放行判据, 等于把同
一决策分叉到两处实现, 正是 的成因。因此本模块对一切放行倾向一律弃权。

判定的语义前提: "未检索到响应"要能推出"不符合", 必须先排除"其实有响应只是没被检索
到"与"这条要求本就不需要响应"两类可能。abstain 清单即是对这两类可能的穷举封堵。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from app.core.ai.gates.base import GateVerdict
from app.core.config import settings

if TYPE_CHECKING:
    from app.core.matcher.requirement_matcher import MatchedPair
    from app.core.review_contracts import ReviewEvidenceBundle

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"
_KNOWN_MODES = (MODE_OFF, MODE_SHADOW, MODE_ENFORCE)

GATE_NAME = "DeterministicNoResponseJudge"

# ---------------------------------------------------------------------------
# 弃权原因码（英文标识符，进 Prometheus label 与结构化日志）
# ---------------------------------------------------------------------------
ABSTAIN_HAS_RESPONSE = "has_response"
ABSTAIN_EXTRACTION_FAILED = "extraction_failed"
ABSTAIN_EVIDENCE_INSUFFICIENT = "evidence_insufficient"
ABSTAIN_EVIDENCE_MISSING = "evidence_missing"
ABSTAIN_RETRIEVAL_STARVATION = "retrieval_starvation"
ABSTAIN_SCORING_ITEM = "scoring_item"
ABSTAIN_KEY_REVIEW_ITEM = "key_review_item"
ABSTAIN_SCORING_LANGUAGE = "scoring_language_in_text"
ABSTAIN_SUPPLEMENTARY_EVIDENCE = "supplementary_evidence_present"
ABSTAIN_COMPOUND_REQUIREMENT = "compound_requirement"
ABSTAIN_FORMAL_REQUIREMENT = "formal_or_procedural_requirement"
ABSTAIN_RELEVANCE_NOT_ACTIONABLE = "relevance_not_actionable"
ABSTAIN_REQUIREMENT_TEXT_TOO_SHORT = "requirement_text_too_short"

# 实质性内容标记: 命中即表示该要求索取的是实体资料/参数/承诺, 而非纯载体形态。
# 词条刻意保持"具体名词"粒度, 禁止收录"文件""资料"等泛词——泛词会让复合判定失效。
_SUBSTANTIVE_MARKERS: tuple[str, ...] = (
    "资质", "证书", "许可证", "资格", "认证", "备案",
    "业绩", "合同", "案例", "履历",
    "人员", "职称", "社保", "劳动合同", "项目经理", "技术负责人",
    "技术方案", "施工方案", "服务方案", "实施方案", "技术参数", "配置",
    "性能指标", "工艺", "设备",
    "报价", "价格", "单价", "费率", "金额", "费用", "税率",
    "承诺", "保证", "售后", "质保", "工期", "交付", "供货", "维保",
    "财务", "审计", "报表", "纳税", "缴纳", "资信",
)

# 评分语义标记: requirement_kind 漏标时的兜底(案例2 -- 报价评分规则未被识别
# 为 scoring_item, 下游按"评标程序"整条放行)。多字词条, 禁止收录裸"分"。
_SCORING_LANGUAGE_MARKERS: tuple[str, ...] = (
    "评分", "得分", "分值", "计分", "满分", "扣分", "加分",
    "评审因素", "评分标准", "评分办法", "分档", "评标办法",
)


@dataclass(frozen=True)
class DeterministicNoResponseOutcome:
    """一次确定性判定的完整结果（含审计证明）。"""

    mode: str
    abstained: bool
    abstain_reasons: tuple[str, ...] = ()
    verdict: Optional[GateVerdict] = None
    needs_review: bool = False

    @property
    def primary_abstain_reason(self) -> str:
        """首个弃权原因；未弃权时为空串。用作 Prometheus label（控制基数）。"""
        return self.abstain_reasons[0] if self.abstain_reasons else ""


def resolve_mode() -> str:
    """解析当前模式；非法取值一律退回 shadow（失效方向必须是"不生效"）。"""
    raw = getattr(settings, "REVIEW_DETERMINISTIC_NO_RESPONSE_MODE", MODE_SHADOW)
    normalized = str(raw or "").strip().lower()
    return normalized if normalized in _KNOWN_MODES else MODE_SHADOW


def _requirement_text_of(pair: "MatchedPair") -> str:
    requirement = getattr(pair, "requirement", None)
    return str(getattr(requirement, "description", "") or "") if requirement else ""


def _hits(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker in text]


def _collect_evidence_abstains(
    pair: "MatchedPair",
    evidence_bundle: Optional["ReviewEvidenceBundle"],
) -> list[str]:
    """证据侧弃权判定：证据链一旦有任何降级迹象即拒绝确定性判定。

    未知的新增 quality_flag 一律按"证据存疑"处理——失效方向必须倒向弃权，
    否则将来任何新增降级信号都会被算法静默忽略。
    """
    reasons: list[str] = []
    response = getattr(pair, "response", None)

    # 检索饥饿: 提取阶段已声明"证据页没被覆盖完", 此时"未找到"不等于"不存在"
    # (判定走 is_starvation_note 单源 helper, 新旧前缀兼容存量数据)
    from app.core.retriever.evidence_priors import is_starvation_note

    starvation_note = str(getattr(response, "relevance_note", "") or "") if response else ""
    flags = list(getattr(evidence_bundle, "quality_flags", None) or [])
    starved = is_starvation_note(starvation_note) or any(
        is_starvation_note(str(flag)) for flag in flags
    )
    if starved:
        reasons.append(ABSTAIN_RETRIEVAL_STARVATION)

    if evidence_bundle is None:
        reasons.append(ABSTAIN_EVIDENCE_MISSING)
        return reasons

    if any(not is_starvation_note(str(flag)) for flag in flags):
        reasons.append(ABSTAIN_EVIDENCE_MISSING)

    # 补充证据存在 = 系统已在别处找到疑似响应材料(扫描件/跨类别援引/独立搜索),
    # "未响应"前提本身已被动摇, 必须交回 LLM 判断其是否构成实质响应
    if (getattr(evidence_bundle, "merged_evidence_text", "") or "").strip():
        reasons.append(ABSTAIN_SUPPLEMENTARY_EVIDENCE)
    elif getattr(evidence_bundle, "has_cross_category_injection", False):
        reasons.append(ABSTAIN_SUPPLEMENTARY_EVIDENCE)

    return reasons


def _collect_requirement_abstains(pair: "MatchedPair", text: str) -> list[str]:
    """要求属性侧弃权判定。"""
    from app.core.extractor.tools.physical_form_classifier import classify_requirement
    from app.core.review_contracts import REVIEWABLE_RELEVANCE

    reasons: list[str] = []
    requirement = getattr(pair, "requirement", None)

    min_len = int(getattr(settings, "REVIEW_DETERMINISTIC_NO_RESPONSE_MIN_TEXT_LEN", 12))
    if len(text.strip()) < max(min_len, 1):
        reasons.append(ABSTAIN_REQUIREMENT_TEXT_TOO_SHORT)

    if getattr(requirement, "requirement_kind", "normal") == "scoring_item":
        reasons.append(ABSTAIN_SCORING_ITEM)
    if bool(getattr(requirement, "is_key_review", 0)):
        reasons.append(ABSTAIN_KEY_REVIEW_ITEM)
    if _hits(text, _SCORING_LANGUAGE_MARKERS):
        reasons.append(ABSTAIN_SCORING_LANGUAGE)

    # 上游 review_relevance 是"这条要求是否实质可审"的既有权威信号。
    # 本模块的"不符合"结论只有在上游同样认为可审时才成立; 上游判 procedural /
    # informational 时"未响应"很可能本就正常, 算法侧不得静默推翻上游判定。
    relevance = getattr(requirement, "review_relevance", None) or REVIEWABLE_RELEVANCE
    if relevance != REVIEWABLE_RELEVANCE:
        reasons.append(ABSTAIN_RELEVANCE_NOT_ACTIONABLE)

    form_verdict = classify_requirement(text)
    if form_verdict.is_physical_form or form_verdict.is_procedural:
        # 复合要求必须按其中最高实质性归类。形式词与实质词同现时,
        # 命中形式词不能证明整条要求已被满足, 也不能证明它未被满足。
        if _hits(text, _SUBSTANTIVE_MARKERS):
            reasons.append(ABSTAIN_COMPOUND_REQUIREMENT)
        else:
            reasons.append(ABSTAIN_FORMAL_REQUIREMENT)

    return reasons


def evaluate_no_response_pair(
    pair: "MatchedPair",
    evidence_bundle: Optional["ReviewEvidenceBundle"] = None,
    *,
    mode: Optional[str] = None,
) -> DeterministicNoResponseOutcome:
    """对单个未响应匹配对做确定性判定（纯函数，无 IO）。

    任一 abstain 条件命中即弃权并把判定交回 LLM。仅当全部条件都未命中时，
    才输出 compliance_level="not" 的建议；否决项额外带 needs_review。
    """
    effective_mode = mode or resolve_mode()
    if effective_mode == MODE_OFF:
        return DeterministicNoResponseOutcome(mode=MODE_OFF, abstained=True)

    reasons: list[str] = []
    if getattr(pair, "has_response", False):
        reasons.append(ABSTAIN_HAS_RESPONSE)

    response = getattr(pair, "response", None)
    if response is not None:
        from app.core.extractor.evidence_verdict import is_evidence_insufficient_content
        from app.core.extractor.extraction_failure import is_extraction_failed_content
        if is_extraction_failed_content(getattr(response, "content", None)):
            reasons.append(ABSTAIN_EXTRACTION_FAILED)
        if is_evidence_insufficient_content(getattr(response, "content", None)):
            reasons.append(ABSTAIN_EVIDENCE_INSUFFICIENT)

    text = _requirement_text_of(pair)
    reasons.extend(_collect_requirement_abstains(pair, text))
    reasons.extend(_collect_evidence_abstains(pair, evidence_bundle))

    if reasons:
        deduped = tuple(dict.fromkeys(reasons))
        return DeterministicNoResponseOutcome(
            mode=effective_mode, abstained=True, abstain_reasons=deduped,
        )

    is_mandatory = bool(getattr(pair, "is_mandatory", False))
    verdict = GateVerdict(
        gate_name=GATE_NAME,
        verdict="enforce_patch",
        severity="enforce",
        reason="未检索到响应且证据完备、要求属实质可审，判定不符合",
        suggested_patch={
            "compliance_level": "not",
            "risk_level": "high" if is_mandatory else "medium",
            "score": 0,
            "is_compliant": False,
        },
        confidence=1.0,
        trace={
            "is_mandatory": is_mandatory,
            "review_relevance": getattr(
                getattr(pair, "requirement", None), "review_relevance", None,
            ),
            "requirement_kind": getattr(
                getattr(pair, "requirement", None), "requirement_kind", None,
            ),
            "evidence_checked": True,
        },
    )
    # 否决项走人工复核队列: 算法即使判定唯一也不得成为否决结论的唯一依据
    return DeterministicNoResponseOutcome(
        mode=effective_mode,
        abstained=False,
        verdict=verdict,
        needs_review=is_mandatory,
    )


def build_enforced_result(
    pair: "MatchedPair",
    outcome: DeterministicNoResponseOutcome,
) -> Optional[Any]:
    """把非弃权结论装配为 ExtractedReviewResult（仅 enforce 模式调用）。

    返回 None 表示不接管，调用方须继续走 LLM 全量路径。
    """
    if outcome.mode != MODE_ENFORCE or outcome.abstained or outcome.verdict is None:
        return None

    from app.schemas.review import ExtractedReviewResult

    patch = outcome.verdict.suggested_patch or {}
    requirement = getattr(pair, "requirement", None)
    summary = str(getattr(requirement, "description", "") or "")[:50]
    comment = (
        "[算法判定] 投标文件未检索到对本要求的响应，且证据链完整、要求属实质可审，"
        "判定为不符合。"
    )
    if outcome.needs_review:
        comment += "本条为否决项，算法结论仅作初判，须人工复核确认。"

    return ExtractedReviewResult(
        requirement_id=pair.requirement_id,
        bid_response_id=pair.response_id,
        category=pair.category,
        requirement_summary=summary,
        response_summary="未找到响应",
        is_compliant=False,
        compliance_level=patch.get("compliance_level", "not"),
        score=int(patch.get("score", 0)),
        comment=comment,
        risk_level=patch.get("risk_level", "medium"),
    )


def record_shadow_comparison(
    pair: "MatchedPair",
    outcome: DeterministicNoResponseOutcome,
    llm_result: Optional[Any],
) -> None:
    """采集算法建议 vs LLM 实际结论的影子对比（fail-safe，异常绝不外抛）。

    观测通道选择 Prometheus 计数 + INFO 结构化日志: 阶段7 起 loguru WARNING+ 会被
    log_sink 写入 system_error_logs 并按 traffic_class 归类, 影子对比是常态化高频
    观测而非故障, 走 WARNING 会污染错误日志表, 因此固定 INFO 级。
    """
    try:
        from loguru import logger

        from app.core.observability.metrics import (
            deterministic_no_response_abstain_total,
            deterministic_no_response_agreement_total,
            deterministic_no_response_disagreement_total,
            deterministic_no_response_outcome_total,
        )

        mode = outcome.mode
        deterministic_no_response_outcome_total.labels(
            mode=mode,
            outcome="abstained" if outcome.abstained else "suggested",
        ).inc()

        if outcome.abstained:
            for reason in outcome.abstain_reasons or ("unspecified",):
                deterministic_no_response_abstain_total.labels(reason=reason).inc()
            logger.info(
                "[确定性未响应判定] mode={} requirement={} 弃权 reasons={}",
                mode, getattr(pair, "requirement_id", None),
                list(outcome.abstain_reasons),
            )
            return

        patch = (outcome.verdict.suggested_patch if outcome.verdict else None) or {}
        for dimension, alg_value in (
            ("compliance_level", str(patch.get("compliance_level", ""))),
            ("risk_level", str(patch.get("risk_level", ""))),
        ):
            llm_value = _normalized_attr(llm_result, dimension)
            if llm_value is None:
                continue
            if alg_value == llm_value:
                deterministic_no_response_agreement_total.labels(
                    dimension=dimension, mode=mode,
                ).inc()
            else:
                deterministic_no_response_disagreement_total.labels(
                    dimension=dimension, alg=alg_value, llm=llm_value,
                ).inc()

        logger.info(
            "[确定性未响应判定] mode={} requirement={} 建议={} needs_review={} "
            "llm_compliance={} llm_risk={}",
            mode,
            getattr(pair, "requirement_id", None),
            {k: v for k, v in patch.items() if not k.startswith("_")},
            outcome.needs_review,
            _normalized_attr(llm_result, "compliance_level"),
            _normalized_attr(llm_result, "risk_level"),
        )
    except Exception:  # noqa: BLE001 - 观测绝不允许影响主审查流程
        pass


def _normalized_attr(source: Optional[Any], field_name: str) -> Optional[str]:
    """取枚举/字符串字段的小写字面值；缺失返回 None。"""
    if source is None:
        return None
    value = getattr(source, field_name, None)
    if value is None:
        return None
    return str(getattr(value, "value", value)).lower()

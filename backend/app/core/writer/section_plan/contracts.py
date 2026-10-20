"""SectionPlan Pydantic contracts.

职责: 定义并校验单章论证计划的数据契约。
不做: 不调用 LLM、不读取数据库、不生成最终章节正文。
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

from app.core.writer.visuals.ir_contracts import (
    MAX_FLOWCHART_NODES,
    MAX_GANTT_PHASE_DURATION_DAYS,
    MAX_GANTT_PHASES,
    MAX_LAYER_ITEMS,
    MAX_LAYERS,
    MAX_NETWORK_EDGES,
    MAX_NETWORK_NODES,
    MAX_ORG_UNITS,
    MAX_SWIM_LANES,
    MAX_SWIM_NODES,
    MAX_WBS_ITEMS,
    MAX_WBS_PACKAGES,
    MAX_ZONES,
    MIN_FLOWCHART_NODES,
    MIN_GANTT_PHASES,
)

PlanItemId = Annotated[
    str,
    Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
]
EvidenceKey = Annotated[
    str,
    Field(min_length=3, max_length=96, pattern=r"^[a-z_]+:[A-Za-z0-9:_-]+$"),
]


class StrictPlanModel(BaseModel):
    """Strict base model for planner input/output."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SectionPlanEvidenceCandidate(StrictPlanModel):
    evidence_key: EvidenceKey
    source_type: Literal[
        "outline_node",
        "clause",
        "industry_profile",
        "material_excerpt",
    ]
    source_id: PositiveInt
    material_id: PositiveInt | None = None
    page_start: PositiveInt | None = None
    page_end: PositiveInt | None = None
    excerpt: str = Field(min_length=1, max_length=600)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_eligible: bool = False

    @model_validator(mode="after")
    def validate_page_range(self) -> "SectionPlanEvidenceCandidate":
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("page_end must be >= page_start")
        return self


class SectionPlanConstraintInput(StrictPlanModel):
    clause_id: PositiveInt
    sub_type: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=2000)
    score_weight: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SectionPlanPromptSnapshot(StrictPlanModel):
    planner_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["registry", "code", "code_fallback_invalid_registry"]
    planner_system_version_id: PositiveInt | None = None
    planner_user_version_id: PositiveInt | None = None
    compose_prompt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SectionPlanRequest(StrictPlanModel):
    project_id: PositiveInt
    outline_node_id: PositiveInt
    node_title: str = Field(min_length=1, max_length=500)
    target_word_count: int = Field(ge=100, le=5000)
    chapter_profile_text: str = Field(default="", max_length=2000)
    industry_profile_text: str = Field(default="", max_length=4000)
    constraints: list[SectionPlanConstraintInput] = Field(default_factory=list, max_length=100)
    evidence_catalog: list[SectionPlanEvidenceCandidate] = Field(default_factory=list, max_length=100)
    prompt_snapshot: SectionPlanPromptSnapshot
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def is_scoring_constraint(item: SectionPlanConstraintInput) -> bool:
    """统一评分约束身份：显式评分 subtype 或存在正分值事实。"""

    return (
        item.sub_type == "scoring_criterion"
        or (
            item.score_weight is not None
            and item.score_weight > 0
        )
    )


class ScoringResponseDraft(StrictPlanModel):
    scoring_point_id: PlanItemId
    clause_id: PositiveInt
    response_actions: list[str] = Field(min_length=1, max_length=6)
    evidence_keys: list[EvidenceKey] = Field(default_factory=list, max_length=8)


class IndustryAnchorDraft(StrictPlanModel):
    anchor_id: PlanItemId
    guidance: str = Field(min_length=2, max_length=300)
    evidence_keys: list[EvidenceKey] = Field(default_factory=list, max_length=5)


class ProjectFactDraft(StrictPlanModel):
    fact_id: PlanItemId
    evidence_key: EvidenceKey
    intended_use: str = Field(min_length=2, max_length=200)


class ArgumentDraft(StrictPlanModel):
    argument_id: PlanItemId
    claim: str = Field(min_length=2, max_length=300)
    scoring_point_ids: list[PlanItemId] = Field(default_factory=list)
    project_fact_ids: list[PlanItemId] = Field(default_factory=list)


class MeasureDraft(StrictPlanModel):
    measure_id: PlanItemId
    action: str = Field(min_length=2, max_length=300)
    steps: list[str] = Field(min_length=1, max_length=8)
    verification: str = Field(min_length=2, max_length=300)
    argument_ids: list[PlanItemId] = Field(default_factory=list)
    project_fact_ids: list[PlanItemId] = Field(default_factory=list)

    @field_validator("steps", mode="before")
    @classmethod
    def coerce_steps_to_list(cls, value: object) -> object:
        """: LLM 偶发把 steps 输出为单个字符串, 归一为单元素列表。"""
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return value


class AcceptanceCriterionDraft(StrictPlanModel):
    criterion_id: PlanItemId
    criterion: str = Field(min_length=2, max_length=300)
    verification_method: str = Field(min_length=2, max_length=300)
    source_clause_ids: list[PositiveInt] = Field(default_factory=list)
    scoring_point_ids: list[PlanItemId] = Field(default_factory=list)
    measure_ids: list[PlanItemId] = Field(default_factory=list)


class MissingInformation(StrictPlanModel):
    field_name: str = Field(min_length=1, max_length=100)
    placeholder: str = Field(pattern=r"^\[待补充:.+\]$", max_length=150)
    reason: str = Field(min_length=2, max_length=200)


class GanttPhaseDraft(StrictPlanModel):
    """甘特图阶段: LLM 显式给出阶段名与工期, 禁止事后从自由文本挖掘。"""

    label: str = Field(min_length=1, max_length=60)
    duration_days: int = Field(ge=1, le=MAX_GANTT_PHASE_DURATION_DAYS)
    milestone: bool = False


class SwimNodeDraft(StrictPlanModel):
    label: str = Field(min_length=1, max_length=60)
    lane: str = Field(min_length=1, max_length=60)


class LayerDraft(StrictPlanModel):
    label: str = Field(min_length=1, max_length=60)
    items: list[str] = Field(default_factory=list, max_length=MAX_LAYER_ITEMS)


class NetworkNodeDraft(StrictPlanModel):
    node_id: str = Field(default="", max_length=32)
    label: str = Field(default="", max_length=60)
    duration_days: int | None = None


class NetworkEdgeDraft(StrictPlanModel):
    source: str = Field(default="", max_length=32)
    target: str = Field(default="", max_length=32)


class WbsPackageDraft(StrictPlanModel):
    label: str = Field(default="", max_length=60)
    items: list[str] = Field(default_factory=list, max_length=MAX_WBS_ITEMS)


class VisualIntentDraft(StrictPlanModel):
    """事实型图示意图: 声明画什么并携带可100%消费的结构化图参数。: 结构完备性(阶段/节点数、工期)在闸门层校验而非 schema 层强制,
 使不合格意图被"丢弃并打补丁"而不是让整个 SectionPlan 校验失败。
 """

    visual_id: PlanItemId
    diagram_type: Literal[
        "flowchart", "gantt", "swimlane", "roadmap", "org_chart", "layered",
        "network", "wbs", "zone_layout",
    ]
    title: str = Field(min_length=2, max_length=100)
    purpose: str = Field(min_length=2, max_length=200)
    measure_ids: list[PlanItemId] = Field(min_length=1, max_length=8)
    placement_hint: PlanItemId | None = None
    template_id: str = Field(default="", max_length=32)
    phases: list[GanttPhaseDraft] = Field(default_factory=list, max_length=MAX_GANTT_PHASES)
    node_labels: list[str] = Field(default_factory=list, max_length=MAX_FLOWCHART_NODES)
    org_lead: str = Field(default="", max_length=60)
    org_units: list[str] = Field(default_factory=list, max_length=MAX_ORG_UNITS)
    lanes: list[str] = Field(default_factory=list, max_length=MAX_SWIM_LANES)
    swim_nodes: list[SwimNodeDraft] = Field(default_factory=list, max_length=MAX_SWIM_NODES)
    layers: list[LayerDraft] = Field(default_factory=list, max_length=MAX_LAYERS)
    network_nodes: list[NetworkNodeDraft] = Field(
        default_factory=list, max_length=MAX_NETWORK_NODES,
    )
    network_edges: list[NetworkEdgeDraft] = Field(
        default_factory=list, max_length=MAX_NETWORK_EDGES,
    )
    wbs_root: str = Field(default="", max_length=60)
    wbs_packages: list[WbsPackageDraft] = Field(
        default_factory=list, max_length=MAX_WBS_PACKAGES,
    )
    zones: list[str] = Field(default_factory=list, max_length=MAX_ZONES)

    @field_validator("node_labels", "org_units", "lanes", "zones")
    @classmethod
    def validate_label_lists(cls, value: list[str]) -> list[str]:
        return [item.strip()[:60] for item in value if item.strip()]


class SectionPlanDraft(StrictPlanModel):
    section_goal: str = Field(min_length=5, max_length=300)
    scoring_responses: list[ScoringResponseDraft] = Field(default_factory=list)
    industry_anchors: list[IndustryAnchorDraft] = Field(default_factory=list)
    project_facts: list[ProjectFactDraft] = Field(default_factory=list)
    arguments: list[ArgumentDraft] = Field(min_length=1, max_length=12)
    measures: list[MeasureDraft] = Field(min_length=1, max_length=20)
    acceptance_criteria: list[AcceptanceCriterionDraft] = Field(min_length=1, max_length=20)
    missing_information: list[MissingInformation] = Field(default_factory=list)
    visuals: list[VisualIntentDraft] = Field(default_factory=list, max_length=2)

    @model_validator(mode="before")
    @classmethod
    def unwrap_single_key_envelope(cls, data: object) -> object:
        """: 解包 {"section_plan": {...}} 类单键信封。

 仅当顶层是单键 dict 且值本身是 dict 时解包; 字段名错误仍按严格模式拒绝。
 """
        if isinstance(data, dict) and len(data) == 1:
            inner = next(iter(data.values()))
            if isinstance(inner, dict):
                return inner
        return data


class SectionPlanResolution(StrictPlanModel):
    mode: Literal[
        "planned_new",
        "planned_reused",
        "planned_sanitized",
        "legacy_flag_off",
        "legacy_bucket",
        "legacy_fallback",
    ]
    section_plan_id: PositiveInt | None = None
    plan: dict[str, Any] | None = None
    failure_code: str | None = None


_NUMERIC_CLAIM_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:日历天|万元|MPa|m/s|rpm|小时|%|％|天|元|分|组|项|台|套|人|名|年|月|日|℃)",
    re.IGNORECASE,
)
_COMMITMENT_ACTION_PREFIX = r"(?:提供|提交|附上|出具|编制|签署)"
_COMMITMENT_DOCUMENT_TERMS = (
    "承诺函",
    "证明材料",
    "培训记录",
    "成本构成表",
    "签字确认",
)


def _normalize_claim_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _source_text(request: SectionPlanRequest) -> str:
    """构造仅来自输入约束/证据的可核验事实文本。"""

    parts: list[str] = []
    for item in request.constraints:
        parts.append(item.content)
        if item.score_weight is not None:
            parts.append(f"{item.score_weight:g}分")
    parts.extend(item.excerpt for item in request.evidence_catalog)
    return _normalize_claim_text("\n".join(parts))


def _sanitize_claim_text(
    text: str,
    *,
    path: str,
    source: str,
    patches: list[dict[str, str]],
) -> str:
    """把无来源具体值/新增承诺文件替换为待核验占位，并记录审计补丁。"""

    def replace_numeric(match: re.Match[str]) -> str:
        original = match.group(0)
        claim = _normalize_claim_text(original)
        if claim in source:
            return original
        replacement = "[待补充:核验数值]"
        patches.append({
            "path": path,
            "kind": "unsupported_numeric_claim",
            "original": original,
            "replacement": replacement,
        })
        return replacement

    sanitized = _NUMERIC_CLAIM_RE.sub(replace_numeric, text)
    # 逐个词扫描，避免后一个有来源词遮蔽前一个无来源词。
    for document_term in _COMMITMENT_DOCUMENT_TERMS:
        if document_term in source:
            continue
        pattern = re.compile(
            rf"{_COMMITMENT_ACTION_PREFIX}.{{0,30}}?({re.escape(document_term)})"
        )
        while True:
            match = pattern.search(sanitized)
            if match is None:
                break
            replacement = "[待补充:核验材料]"
            patches.append({
                "path": path,
                "kind": "unsupported_commitment_document",
                "original": document_term,
                "replacement": replacement,
            })
            start, end = match.span(1)
            sanitized = sanitized[:start] + replacement + sanitized[end:]
    return sanitized


def _sanitize_payload_claims(
    payload: dict[str, Any],
    request: SectionPlanRequest,
    patches: list[dict[str, str]],
) -> None:
    """原位净化会进入正文规划的自然语言字段。"""

    source = _source_text(request)

    def sanitize(value: str, path: str) -> str:
        return _sanitize_claim_text(
            value,
            path=path,
            source=source,
            patches=patches,
        )

    payload["section_goal"] = sanitize(payload["section_goal"], "section_goal")
    for index, response in enumerate(payload["scoring_responses"]):
        response["response_actions"] = [
            sanitize(value, f"scoring_responses[{index}].response_actions[{action_index}]")
            for action_index, value in enumerate(response["response_actions"])
        ]
    for index, anchor in enumerate(payload["industry_anchors"]):
        anchor["guidance"] = sanitize(
            anchor["guidance"],
            f"industry_anchors[{index}].guidance",
        )
    for index, fact in enumerate(payload["project_facts"]):
        fact["intended_use"] = sanitize(
            fact["intended_use"],
            f"project_facts[{index}].intended_use",
        )
    for index, argument in enumerate(payload["arguments"]):
        argument["claim"] = sanitize(
            argument["claim"],
            f"arguments[{index}].claim",
        )
    for index, measure in enumerate(payload["measures"]):
        measure["action"] = sanitize(
            measure["action"],
            f"measures[{index}].action",
        )
        measure["steps"] = [
            sanitize(value, f"measures[{index}].steps[{step_index}]")
            for step_index, value in enumerate(measure["steps"])
        ]
        measure["verification"] = sanitize(
            measure["verification"],
            f"measures[{index}].verification",
        )
    for index, criterion in enumerate(payload["acceptance_criteria"]):
        criterion["criterion"] = sanitize(
            criterion["criterion"],
            f"acceptance_criteria[{index}].criterion",
        )
        criterion["verification_method"] = sanitize(
            criterion["verification_method"],
            f"acceptance_criteria[{index}].verification_method",
        )
    for index, visual in enumerate(payload.get("visuals", [])):
        visual["title"] = sanitize(visual["title"], f"visuals[{index}].title")
        visual["purpose"] = sanitize(visual["purpose"], f"visuals[{index}].purpose")


def _gate_flowchart_intent(
    intent: VisualIntentDraft,
    referenced: list[MeasureDraft],
    intent_payload: dict[str, Any],
    path: str,
    patches: list[dict[str, str]],
) -> bool:
    """流程图闸门: 归一出最终节点标签写回 payload, 保证编译器100%可消费。

    节点来源优先级: LLM 显式 node_labels > 引用措施的 steps;
    超上限时降级为按 measure.action 聚合(粗粒度全景)而非直接失败。
    """

    labels = list(intent.node_labels)
    if not labels:
        from app.core.writer.visuals.intent_templates import flowchart_template_labels

        labels = flowchart_template_labels(intent.template_id)
    if not labels:
        labels = [
            str(step).strip()[:60]
            for measure in referenced
            for step in measure.steps
            if str(step).strip()
        ]
    if len(labels) > MAX_FLOWCHART_NODES:
        aggregated = [
            measure.action.strip()[:60]
            for measure in referenced
            if measure.action.strip()
        ]
        if MIN_FLOWCHART_NODES <= len(aggregated) <= MAX_FLOWCHART_NODES:
            patches.append({
                "path": path,
                "kind": "visual_degraded_aggregated",
                "original": f"{len(labels)} steps",
                "replacement": f"{len(aggregated)} measure actions",
            })
            labels = aggregated
        else:
            return False
    if len(labels) < MIN_FLOWCHART_NODES:
        return False
    intent_payload["node_labels"] = labels
    return True


def _gate_gantt_intent(
    intent: VisualIntentDraft,
    intent_payload: dict[str, Any],
) -> bool:
    """甘特图闸门: phases 必须由 LLM 显式给出且结构完备(数量/工期)。"""

    phases = intent.phases
    if not (MIN_GANTT_PHASES <= len(phases) <= MAX_GANTT_PHASES):
        return False
    intent_payload["phases"] = [
        {"label": phase.label, "duration_days": phase.duration_days}
        for phase in phases
    ]
    return True


def _gate_visual_intents(
    draft: SectionPlanDraft,
    payload: dict[str, Any],
    patches: list[dict[str, str]],
) -> None:
    """确定性图示闸门: 覆盖 IR 编译器全部拒绝条件, 保证"通过即可出图"。: 上游闸门必须覆盖下游消费者的全部拒绝条件,
 否则失败后置到渲染前无修复机会的环节。
 不合格 intent 丢弃并记录补丁, 不影响 SectionPlan 主体。
 """

    measures_by_id = {item.measure_id: item for item in draft.measures}
    kept: list[dict[str, Any]] = []
    for index, (intent, intent_payload) in enumerate(zip(
        draft.visuals,
        payload.get("visuals", []),
        strict=True,
    )):
        path = f"visuals[{index}]"
        unknown_measures = [
            measure_id
            for measure_id in intent.measure_ids
            if measure_id not in measures_by_id
        ]
        if unknown_measures:
            patches.append({
                "path": path,
                "kind": "invalid_visual_intent",
                "original": intent.visual_id,
                "replacement": "removed",
            })
            continue
        referenced = [measures_by_id[mid] for mid in intent.measure_ids]
        if intent.diagram_type == "flowchart":
            gate_ok = _gate_flowchart_intent(
                intent, referenced, intent_payload, path, patches,
            )
        elif intent.diagram_type == "gantt":
            gate_ok = _gate_gantt_intent(intent, intent_payload)
        else:
            from app.core.writer.visuals.intent_gate import gate_named_diagram_intent

            gate_ok = gate_named_diagram_intent(intent.diagram_type, intent_payload)
        if not gate_ok:
            patches.append({
                "path": path,
                "kind": "visual_gate_failed",
                "original": intent.visual_id,
                "replacement": "removed",
            })
            continue
        kept.append(intent_payload)
    payload["visuals"] = kept


def validate_and_materialize_section_plan(
    draft: SectionPlanDraft,
    request: SectionPlanRequest,
) -> dict[str, Any]:
    """Validate references and return a serializable plan payload."""

    evidence_by_key = {
        item.evidence_key: item
        for item in request.evidence_catalog
    }
    constraint_ids = {item.clause_id for item in request.constraints}
    scoring_clause_ids = {
        item.clause_id
        for item in request.constraints
        if is_scoring_constraint(item)
    }
    payload = draft.model_dump(mode="json")
    safety_patches: list[dict[str, str]] = []
    valid_fact_ids: set[str] = set()
    kept_facts: list[dict[str, Any]] = []
    for fact_index, (fact, fact_payload) in enumerate(zip(
        draft.project_facts,
        payload["project_facts"],
        strict=True,
    )):
        evidence = evidence_by_key.get(fact.evidence_key)
        if evidence is None:
            raise ValueError(f"unknown evidence_key: {fact.evidence_key}")
        if not evidence.fact_eligible:
            safety_patches.append({
                "path": f"project_facts[{fact_index}]",
                "kind": "ineligible_project_fact",
                "original": fact.evidence_key,
                "replacement": "removed",
            })
            continue
        valid_fact_ids.add(fact.fact_id)
        kept_facts.append(fact_payload)
    payload["project_facts"] = kept_facts
    for argument in payload["arguments"]:
        argument["project_fact_ids"] = [
            fact_id
            for fact_id in argument["project_fact_ids"]
            if fact_id in valid_fact_ids
        ]
    for measure in payload["measures"]:
        measure["project_fact_ids"] = [
            fact_id
            for fact_id in measure["project_fact_ids"]
            if fact_id in valid_fact_ids
        ]

    all_evidence_refs: list[str] = []
    for response, response_payload in zip(
        draft.scoring_responses,
        payload["scoring_responses"],
        strict=True,
    ):
        if response.clause_id not in scoring_clause_ids:
            raise ValueError(
                f"unknown scoring clause_id: {response.clause_id}"
            )
        clause_key = f"clause:{response.clause_id}"
        if clause_key not in evidence_by_key:
            raise ValueError(f"unknown evidence_key: {clause_key}")
        evidence_keys = list(response.evidence_keys)
        if clause_key not in evidence_keys:
            evidence_keys.append(clause_key)
        response_payload["evidence_keys"] = evidence_keys
        all_evidence_refs.extend(evidence_keys)
    for anchor in draft.industry_anchors:
        all_evidence_refs.extend(anchor.evidence_keys)
    for key in all_evidence_refs:
        if key not in evidence_by_key:
            raise ValueError(f"unknown evidence_key: {key}")

    for criterion in draft.acceptance_criteria:
        unknown_clause_ids = set(criterion.source_clause_ids) - constraint_ids
        if unknown_clause_ids:
            raise ValueError(
                "unknown acceptance source_clause_ids: "
                f"{sorted(unknown_clause_ids)}"
            )

    _gate_visual_intents(draft, payload, safety_patches)
    _sanitize_payload_claims(payload, request, safety_patches)
    if safety_patches:
        payload["safety_patches"] = safety_patches
    return payload


__all__ = [
    "GanttPhaseDraft",
    "SectionPlanDraft",
    "SectionPlanEvidenceCandidate",
    "SectionPlanRequest",
    "SectionPlanResolution",
    "VisualIntentDraft",
    "is_scoring_constraint",
    "validate_and_materialize_section_plan",
]

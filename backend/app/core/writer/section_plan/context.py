"""SectionPlan request context builder.

职责: 从章节、约束和证据目录构建稳定的规划请求。
不做: 不调用 LLM、不保存计划、不消费前文摘要进入输入指纹。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from loguru import logger

from app.core.writer.section_plan.contracts import (
    SectionPlanConstraintInput,
    SectionPlanEvidenceCandidate,
    SectionPlanRequest,
)

# SectionPlan 契约边界(与 contracts.py 的 Field 约束一致): 越界输入在构建层收敛。
_MAX_CONSTRAINTS = 100
_MAX_EVIDENCE = 100
_MAX_CONSTRAINT_CONTENT_CHARS = 2000
_MAX_EXCERPT_CHARS = 600
_MAX_NODE_TITLE_CHARS = 500
_MAX_CHAPTER_PROFILE_CHARS = 2000
_MAX_INDUSTRY_PROFILE_CHARS = 4000
_MIN_TARGET_WORDS = 100
_MAX_TARGET_WORDS = 5000
_NON_FACTUAL_CLAUSE_SUBTYPES = frozenset({
    "industry_profile",
    "differentiation_hint",
})


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _business_metadata(item: Any) -> dict[str, Any]:
    """读取业务元数据。: ORM 声明模型的 ``metadata`` 属性是 SQLAlchemy 表注册对象，
 必须优先读 ``metadata_json``；仅当替代值是真正的 dict 时才采纳。
 """
    raw = _value(item, "metadata_json", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if not isinstance(raw, dict):
        alt = _value(item, "metadata", None)
        raw = alt if isinstance(alt, dict) else None
    return raw or {}


def _safe_score_weight(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if float(value) >= 0 else None


def _constraint_fact_eligible(item: SectionPlanConstraintInput) -> bool:
    """按来源语义判定约束能否作为项目/招标事实。: ``emphasis_point`` 不能按 subtype 一律视为建议；提取链已确认且
 ``writing_relevance=direct`` 的强调要求是权威招标事实。行业画像与差异化
 提示仍属于生成建议，禁止作为项目事实。
 """

    if item.sub_type in _NON_FACTUAL_CLAUSE_SUBTYPES:
        return False
    if item.sub_type == "emphasis_point":
        return item.metadata.get("writing_relevance") == "direct"
    return True


def _build_constraints(items: Iterable[Any]) -> list[SectionPlanConstraintInput]:
    constraints: list[SectionPlanConstraintInput] = []
    for item in items:
        content = str(_value(item, "content", "") or "").strip()
        if not content:
            continue
        constraints.append(
            SectionPlanConstraintInput(
                clause_id=int(_value(item, "clause_id", _value(item, "id"))),
                sub_type=str(_value(item, "sub_type", "unknown") or "unknown")[:50],
                content=content[:_MAX_CONSTRAINT_CONTENT_CHARS],
                score_weight=_safe_score_weight(_value(item, "score_weight", None)),
                metadata=_business_metadata(item),
            )
        )
    return sorted(constraints, key=lambda c: c.clause_id)[:_MAX_CONSTRAINTS]


def _build_evidence(items: Iterable[Any]) -> list[SectionPlanEvidenceCandidate]:
    evidence: list[SectionPlanEvidenceCandidate] = []
    for item in items:
        excerpt = str(_value(item, "excerpt", "") or "").strip()[:_MAX_EXCERPT_CHARS]
        if not excerpt:
            continue
        provided_sha = _value(item, "content_sha256", None)
        evidence.append(
            SectionPlanEvidenceCandidate(
                evidence_key=str(_value(item, "evidence_key")),
                source_type=str(_value(item, "source_type", "material_excerpt")),
                source_id=int(_value(item, "source_id")),
                material_id=_value(item, "material_id", None),
                page_start=_value(item, "page_start", None),
                page_end=_value(item, "page_end", None),
                excerpt=excerpt,
                content_sha256=str(provided_sha or _sha256_text(excerpt)),
                fact_eligible=bool(_value(item, "fact_eligible", False)),
            )
        )
    return sorted(evidence, key=lambda e: e.evidence_key)[:_MAX_EVIDENCE]


def _build_constraint_evidence(
    constraints: Iterable[SectionPlanConstraintInput],
) -> list[SectionPlanEvidenceCandidate]:
    """为每条 planner 可见约束生成同源稳定 evidence key。: 约束既进入 constraints_json 又可被 LLM 通过 clause_id 引用，
 因此必须同步登记为 ``clause:{id}``，不能只登记素材 evidence。
 """

    return [
        SectionPlanEvidenceCandidate(
            evidence_key=f"clause:{item.clause_id}",
            source_type="clause",
            source_id=item.clause_id,
            excerpt=item.content[:_MAX_EXCERPT_CHARS],
            content_sha256=_sha256_text(item.content),
            fact_eligible=_constraint_fact_eligible(item),
        )
        for item in constraints
    ]


def _merge_evidence_catalogs(
    *catalogs: Iterable[SectionPlanEvidenceCandidate],
) -> list[SectionPlanEvidenceCandidate]:
    """按 evidence_key 合并目录；约束证据优先，稳定输入顺序后统一截断。"""

    by_key: dict[str, SectionPlanEvidenceCandidate] = {}
    for catalog in catalogs:
        for item in catalog:
            by_key.setdefault(item.evidence_key, item)
    merged = list(by_key.values())
    if len(merged) > _MAX_EVIDENCE:
        logger.warning(
            "SectionPlan evidence_catalog truncated: total={}, kept={}, dropped={}",
            len(merged),
            _MAX_EVIDENCE,
            len(merged) - _MAX_EVIDENCE,
        )
    return merged[:_MAX_EVIDENCE]


def _stable_fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_section_plan_request(
    *,
    project_id: int,
    node: Any,
    constraints: Iterable[Any],
    material_entries: Iterable[Any],
    industry_profile_text: str,
    prompt_snapshot: dict[str, Any],
    previous_summaries: list[str] | None = None,
) -> SectionPlanRequest:
    """Build a deterministic SectionPlanRequest.

    ``previous_summaries`` is accepted for caller symmetry but deliberately
    excluded from the fingerprint to keep full and single-section planning
    consistent.
    """

    del previous_summaries
    constraint_inputs = _build_constraints(constraints)
    evidence_catalog = _merge_evidence_catalogs(
        _build_constraint_evidence(constraint_inputs),
        _build_evidence(material_entries),
    )
    chapter_profile = _value(node, "chapter_profile", None)
    chapter_profile_text = (
        chapter_profile
        if isinstance(chapter_profile, str)
        else json.dumps(chapter_profile or {}, ensure_ascii=False, sort_keys=True)
    )
    raw_target_words = int(_value(node, "target_word_count", 1000) or 1000)
    fingerprint_payload = {
        "project_id": int(project_id),
        "outline_node_id": int(_value(node, "id")),
        "node_title": str(_value(node, "title", ""))[:_MAX_NODE_TITLE_CHARS],
        "target_word_count": max(_MIN_TARGET_WORDS, min(_MAX_TARGET_WORDS, raw_target_words)),
        "chapter_profile_text": chapter_profile_text[:_MAX_CHAPTER_PROFILE_CHARS],
        "industry_profile_text": (industry_profile_text or "")[:_MAX_INDUSTRY_PROFILE_CHARS],
        "constraints": [item.model_dump(mode="json") for item in constraint_inputs],
        "evidence_catalog": [item.model_dump(mode="json") for item in evidence_catalog],
        "prompt_snapshot": prompt_snapshot,
    }
    return SectionPlanRequest(
        **fingerprint_payload,
        input_fingerprint=_stable_fingerprint(fingerprint_payload),
    )


__all__ = ["build_section_plan_request"]

"""
职责: 按 facet_states+库存对 extracted 终态五元组做确定性原子重算
不做: LLM、检索、改写审查意见正文
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from app.core.ai.gates.review.claim_response_consistency import _comment_has_denial
from app.core.bid_envelope import BID_PART_LABELS
from app.core.retriever.evidence_coverage import EvidenceFacet, build_evidence_facets
from app.core.retriever.material_key import MATCH_STRONG, match_material_in_text
from app.core.review_contracts import parse_json_list

EXISTENCE_FLOOR_SCORE = 40
PRESENCE_FOUND = "found"
_DENIAL_EXTRA = ("未发现", "未提供")
REBUTTAL_MARKERS: tuple[str, ...] = (
    "主体不符",
    "非投标人自身",
    "已过期",
    "等级不足",
    "原件形式",
)


def _has_rebuttal(comment: str) -> bool:
    return any(marker in (comment or "") for marker in REBUTTAL_MARKERS)


def denies_existence(comment: str) -> bool:
    text = comment or ""
    if _comment_has_denial(text):
        return True
    return any(token in text for token in _DENIAL_EXTRA)


def collect_existence_conflicts(
    comment: str,
    material_atoms: Sequence[Mapping[str, Any]],
    *,
    facet_states: Sequence[Mapping[str, Any]] = (),
    facets: Sequence[EvidenceFacet] = (),
) -> tuple[dict[str, Any], ...]:
    """存在性否认 ∧ 材料级强命中同一材料 → 冲突原子。适用性反驳不计入。"""
    if _has_rebuttal(comment) or not denies_existence(comment):
        return ()
    facet_keys = [facet.key for facet in facets if getattr(facet, "key", "")]
    found_keys = [
        str(state.get("facet_key") or state.get("canonical_material_key") or "")
        for state in facet_states
        if str(state.get("presence") or "") == PRESENCE_FOUND
    ]
    hits: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for atom in material_atoms:
        if not isinstance(atom, Mapping):
            continue
        if str(atom.get("grade") or "") != "material":
            continue
        kind = str(atom.get("kind") or atom.get("canonical_material_key") or "")
        if not kind:
            continue
        if not _atom_matches_clause(kind, atom, facet_keys, found_keys):
            continue
        key = (kind, atom.get("page", atom.get("physical_page")), atom.get("chunk_id"))
        if key in seen:
            continue
        seen.add(key)
        hits.append(dict(atom))
    if hits:
        return tuple(hits)
    if found_keys and (not facet_keys or any(
        _strong_or_equal(facet_key, found_key)
        for facet_key in facet_keys
        for found_key in found_keys
        if found_key
    )):
        for state in facet_states:
            if str(state.get("presence") or "") != PRESENCE_FOUND:
                continue
            refs = list(state.get("evidence_refs") or ())
            first = refs[0] if refs and isinstance(refs[0], Mapping) else {}
            hits.append({
                "kind": str(state.get("canonical_material_key") or state.get("facet_key") or ""),
                "grade": "material",
                "page": first.get("page"),
                "physical_page": first.get("page"),
                "chunk_id": first.get("chunk_id"),
                "document_id": first.get("document_id"),
            })
    return tuple(hits)


def format_conflict_location_note(atoms: Sequence[Mapping[str, Any]]) -> str:
    labels: list[str] = []
    for atom in atoms:
        page = atom.get("page", atom.get("physical_page"))
        part = atom.get("bid_part")
        if page is None:
            continue
        page_label = f"第{int(page)}页"
        if part:
            volume = BID_PART_LABELS.get(str(part), str(part))
            page_label = f"{volume}{page_label}"
        if page_label not in labels:
            labels.append(page_label)
        if len(labels) >= 3:
            break
    if not labels:
        return "投标文件其他位置已见同类材料，存在性已核实，符合性待人工核对。"
    return (
        f"投标文件{ '、'.join(labels) }已核实存在该材料，"
        "存在性不得再判未提供，请核适用性。"
    )


def reconcile_existence_final_state(
    extracted: Any,
    pair: Any,
    evidence: Optional[Mapping[str, Any]] = None,
) -> tuple[Any, Optional[str]]:
    """
    冲突且仍否认存在性时写入一致五元组；意见正文不改写。
    返回 (extracted, failure_state 或 None)。
    """
    evidence = evidence or {}
    comment = str(getattr(extracted, "comment", "") or "")
    atoms = [
        atom for atom in (evidence.get("ledger_atoms") or ())
        if isinstance(atom, Mapping)
    ]
    facet_states = _facet_states_of(pair, evidence)
    facets = _facets_of(pair)
    conflicts = collect_existence_conflicts(
        comment, atoms, facet_states=facet_states, facets=facets
    )
    if not conflicts:
        return extracted, None

    compliance = str(getattr(extracted, "compliance_level", "") or "")
    if compliance == "fully" and not denies_existence(comment):
        return extracted, None

    score = int(getattr(extracted, "score", 0) or 0)
    risk = str(getattr(extracted, "risk_level", "") or "medium")
    updates: dict[str, Any] = {}
    if compliance in {"not", "missing"}:
        updates["compliance_level"] = "partially"
        updates["is_compliant"] = False
    updates["score"] = max(score, EXISTENCE_FLOOR_SCORE)
    if risk == "high" and _risk_only_from_absence(comment):
        updates["risk_level"] = "medium"
    note = format_conflict_location_note(conflicts)
    existing_note = str(getattr(extracted, "reconcile_note", "") or "")
    updates["reconcile_note"] = (
        f"{existing_note}；{note}" if existing_note and note not in existing_note else note
    )
    flag = str(getattr(extracted, "consistency_flag", "") or "")
    if flag in {"", "unchecked", "aligned"}:
        updates["consistency_flag"] = "needs_review"
    extracted = _apply_updates(extracted, updates)
    return extracted, "needs_review"


def _atom_matches_clause(
    kind: str,
    atom: Mapping[str, Any],
    facet_keys: Sequence[str],
    found_keys: Sequence[str],
) -> bool:
    if not facet_keys and not found_keys:
        return True
    excerpt = str(atom.get("excerpt") or "")
    for key in (*facet_keys, *found_keys):
        if not key:
            continue
        if key == kind or _strong_or_equal(key, kind) or (
            excerpt and match_material_in_text(key, excerpt) == MATCH_STRONG
        ):
            return True
    return False


def _strong_or_equal(left: str, right: str) -> bool:
    if left == right:
        return True
    return match_material_in_text(left, right) == MATCH_STRONG


def _risk_only_from_absence(comment: str) -> bool:
    if _has_rebuttal(comment):
        return False
    return denies_existence(comment)


def _facet_states_of(pair: Any, evidence: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = evidence.get("facet_states")
    if not raw:
        response = getattr(pair, "response", None)
        raw = getattr(response, "facet_states", None) if response is not None else None
    if not raw:
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _facets_of(pair: Any) -> tuple[EvidenceFacet, ...]:
    requirement = getattr(pair, "requirement", None)
    if requirement is None:
        return ()
    return tuple(build_evidence_facets(
        str(getattr(requirement, "description", "") or ""),
        parse_json_list(getattr(requirement, "sub_requirements", None)),
        keywords=parse_json_list(getattr(requirement, "keywords", None)),
    ))


def _apply_updates(extracted: Any, updates: Mapping[str, Any]) -> Any:
    copier = getattr(extracted, "model_copy", None)
    if callable(copier):
        return copier(update=dict(updates))
    for key, value in updates.items():
        try:
            setattr(extracted, key, value)
        except Exception:
            continue
    return extracted

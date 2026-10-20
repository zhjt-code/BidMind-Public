"""
职责: Q7 实体一致性检查——同一槽位(项目名/编号/金额/工期…)多值冲突
不做: 不跨文档比对(信封收敛层职责)、不判断哪个值正确
机制: 槽位定义在 rules/entity_slots.yaml, 归一化在 normalizers.py;
      金额/工期/名称是同一台账机制的夹具, 禁止为单一实体建专用检查器
"""

from __future__ import annotations

import re
from typing import Any

from app.core.document_qa.contracts import (
    paragraph_hash,
    CheckerOutcome,
    CheckerStatus,
    DocumentModel,
    Evidence,
    EvidenceItem,
    Finding,
    QaCategory,
    QaCertainty,
    QaSeverity,
)
from app.core.document_qa.author_spans import grid_rows, line_for_offset, outline_nodes, prose_spans
from app.core.document_qa.evidence_geom import coerce_bbox, locate_bbox
from app.core.document_qa.normalizers import normalize_slot_value

CODE = "consistency"

_MAX_OCCURRENCES_PER_SLOT = 40
_SEVERITY_MAP = {"P0": QaSeverity.P0, "P1": QaSeverity.P1, "P2": QaSeverity.P2, "P3": QaSeverity.P3}


def _slot_excerpt(page_num: int, paragraph: str, span: Any, matched: re.Match, raw_value: str) -> tuple[int, str]:
    """取值落在哪一行, 摘录就取哪一行。禁止把后续紧邻段拼进证据。"""
    if span is None:
        start = max(0, matched.start() - 10)
        return page_num, paragraph[start:matched.end() + 15]
    line = line_for_offset(span, matched.start(1))
    if line is None or not line.text.strip():
        start = max(0, matched.start() - 10)
        return page_num, paragraph[start:matched.end() + 15]
    local = line.text.strip()
    if raw_value and raw_value in local:
        index = local.find(raw_value)
        start = max(0, index - 10)
        return line.page, local[start:index + len(raw_value) + 15]
    return line.page, local[:80]


def collect_slot_ledger(
    model: DocumentModel,
    rules: dict,
) -> dict[str, dict[str, Any]]:
    """扫描正文跨度与表格视觉行构建实体槽位台账。

    同一视觉行的单元格仍拼在一起, 标签和取值分列时才能进槽位; 不跨行拼接。
    ponytail: 同一视觉行里两个无关单元格仍可能被一次正则读到。升级路径是单元格角色。
    返回 {slot_key: {"label","severity","occurrences":[{value,normalized,page,excerpt,bbox}]}}
    台账同时供本检查器(文内冲突)与信封收敛层(跨卷冲突)消费。
    """
    ledger: dict[str, dict[str, Any]] = {}
    slot_root = rules.get("entity_slots") or {}
    org_cfg = slot_root.get("org_name") if isinstance(slot_root.get("org_name"), dict) else None
    units: list[tuple[int, str, Any]] = []
    for span in prose_spans(model):
        units.append((span.page, span.text, span))
    for row in grid_rows(model):
        units.append((row.page, row.text, None))
    for slot in slot_root.get("slots", []):
        key = str(slot.get("key") or "")
        if not key:
            continue
        kind = str(slot.get("kind") or "text")
        patterns = [re.compile(p) for p in slot.get("patterns", [])]
        occurrences: list[dict[str, Any]] = []
        for page_num, paragraph, span in units:
            for pattern in patterns:
                for matched in pattern.finditer(paragraph):
                    raw_value = matched.group(1)
                    unit = matched.group(2) if matched.lastindex and matched.lastindex >= 2 else None
                    normalized = normalize_slot_value(
                        kind, raw_value, unit,
                        org_cfg if kind == "org_name" else None,
                    )
                    if normalized is None:
                        continue
                    hit_page, excerpt = _slot_excerpt(page_num, paragraph, span, matched, raw_value)
                    found = locate_bbox(model, hit_page, excerpt)
                    scope = ""
                    if str(slot.get("conflict_scope") or "") == "paragraph":
                        scope = paragraph_hash(paragraph)
                    occurrences.append({
                        "value": (raw_value + (unit or "")).strip(),
                        "normalized": normalized,
                        "page": hit_page,
                        "excerpt": excerpt,
                        "bbox": list(found) if found else None,
                        "scope": scope,
                    })
                    if len(occurrences) >= _MAX_OCCURRENCES_PER_SLOT:
                        break
                if len(occurrences) >= _MAX_OCCURRENCES_PER_SLOT:
                    break
            if len(occurrences) >= _MAX_OCCURRENCES_PER_SLOT:
                break
        if occurrences:
            ledger[key] = {
                "label": str(slot.get("label") or key),
                "severity": str(slot.get("severity") or "P1"),
                "occurrences": occurrences,
            }
    return ledger


def slot_conflict_findings(
    ledger: dict[str, dict[str, Any]],
    *,
    rule_code: str = "DOC-CONS-001",
    cross_document: bool = False,
) -> list[Finding]:
    """从台账产出同槽位多值冲突(文内与跨卷共用同一判定)。"""
    findings: list[Finding] = []
    for slot_key, entry in ledger.items():
        scoped: dict[str, list[dict[str, Any]]] = {}
        for occ in entry["occurrences"]:
            scoped.setdefault(str(occ.get("scope") or ""), []).append(occ)
        groups = list(scoped.values()) if any(occ.get("scope") for occ in entry["occurrences"]) else [entry["occurrences"]]
        for group in groups:
            findings.extend(_conflict_from_group(
                slot_key, entry, group,
                rule_code=rule_code, cross_document=cross_document,
            ))
    return findings


def _conflict_from_group(
    slot_key: str,
    entry: dict[str, Any],
    occurrences: list[dict[str, Any]],
    *,
    rule_code: str,
    cross_document: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    by_normalized: dict[str, dict[str, Any]] = {}
    for occ in occurrences:
        by_normalized.setdefault(occ["normalized"], occ)
    if len(by_normalized) < 2:
        return findings
    if cross_document:
        docs = {occ.get("document_id") for occ in by_normalized.values()}
        if len(docs) < 2:
            return findings
    distinct = list(by_normalized.items())[:3]
    values_text = " / ".join(f"「{occ['value']}」" for _, occ in distinct)
    first = distinct[0][1]
    second = distinct[1][1]
    scope_text = "跨卷" if cross_document else "文中"

    def _item(occ: dict[str, Any], role: str) -> EvidenceItem:
        return EvidenceItem(
            excerpt=str(occ.get("excerpt") or occ.get("value") or ""),
            page=occ.get("page"),
            document_id=occ.get("document_id"),
            bid_part=occ.get("bid_part"),
            bbox=coerce_bbox(occ.get("bbox")),
            role=role,
        )

    findings.append(Finding(
        rule_code=rule_code,
        category=QaCategory.CONSISTENCY,
        severity=_SEVERITY_MAP.get(entry["severity"], QaSeverity.P1),
        certainty=QaCertainty.PROBABLE,
        title=f"{entry['label']}{scope_text}取值不一致",
        message=(
            f"{entry['label']}在{scope_text}出现{len(by_normalized)}个不同取值: {values_text}, "
            "可能存在实质性矛盾, 请人工核对"
        ),
        suggestion=f"统一{entry['label']}的书写, 确认哪个取值正确",
        evidence=Evidence(
            kind="pair",
            items=(_item(first, "primary"), _item(second, "comparison")),
            expected=first["value"],
            actual=second["value"],
        ),
        anchor=f"slot_conflict:{slot_key}:{'x' if cross_document else 'i'}:{first.get('scope') or ''}",
        closed_measurement=True,
    ))
    return findings


def _consecutive_gap(longer: str, shorter: str, max_cut: int) -> int:
    """shorter 是 longer 删掉连续 max_cut 字以内的结果时, 返回删掉的字数。"""
    gap = len(longer) - len(shorter)
    if gap < 1 or gap > max_cut or not shorter:
        return 0
    for start in range(0, len(longer) - gap + 1):
        if longer[:start] + longer[start + gap:] == shorter:
            return gap
    return 0


def _title_variant_findings(model: DocumentModel, rules: dict) -> list[Finding]:
    """两个拼完整的表外大纲标题只差连续少量字, 才是写法不一致。

    正文或单元格里的词干不算。标题原文相同不算。
    """
    cfg = ((rules.get("entity_slots") or {}).get("title_variant") or {})
    min_chars = int(cfg.get("min_chars") or 8)
    max_cut = int(cfg.get("max_cut") or 4)
    titles = [
        heading for heading in outline_nodes(model, rules)
        if len(heading.title) >= min_chars
    ]
    findings: list[Finding] = []
    seen: set[str] = set()
    for index, left in enumerate(titles):
        for right in titles[index + 1:]:
            if left.title == right.title:
                continue
            longer, shorter = (left, right) if len(left.title) >= len(right.title) else (right, left)
            cut = _consecutive_gap(longer.title, shorter.title, max_cut)
            if cut == 0 or len(shorter.title) < min_chars:
                continue
            key = f"{longer.title}|{shorter.title}"
            if key in seen:
                continue
            seen.add(key)
            findings.append(Finding(
                rule_code="DOC-CONS-001",
                category=QaCategory.CONSISTENCY,
                severity=QaSeverity.P1,
                certainty=QaCertainty.PROBABLE,
                title="标题与正文写法不一致",
                message=f"标题「{longer.title}」与标题「{shorter.title}」只差连续{cut}字, 可能不是同一写法",
                suggestion="统一这两处标题的写法",
                evidence=Evidence(
                    kind="pair",
                    items=(
                        EvidenceItem(
                            excerpt=longer.title, page=longer.page, bbox=longer.bbox, role="primary",
                        ),
                        EvidenceItem(
                            excerpt=shorter.title, page=shorter.page, bbox=shorter.bbox, role="comparison",
                        ),
                    ),
                    expected=longer.title,
                    actual=shorter.title,
                ),
                anchor=f"title_variant:{longer.title}:{shorter.title}",
                closed_measurement=True,
            ))
    return findings[:8]


def check_consistency(model: DocumentModel, rules: dict) -> CheckerOutcome:
    ledger = collect_slot_ledger(model, rules)
    findings = slot_conflict_findings(ledger)
    findings.extend(_title_variant_findings(model, rules))
    outcome = CheckerOutcome(code=CODE, status=CheckerStatus.OK, findings=findings)
    return outcome

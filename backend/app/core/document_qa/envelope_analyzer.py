"""
职责: 信封级收敛——跨卷实体槽位冲突、跨卷精确重复、发现项汇总统计
不做: 不解析文档、不落库; 整本信封(单文档)跨卷比对自然为空集,
      禁止为整本/分卷写不同分支(档案驱动, 见 core/bid_envelope.py)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.document_qa.contracts import (
    Evidence,
    EvidenceItem,
    Finding,
    QaCategory,
    QaCertainty,
    QaSeverity,
)
from app.core.document_qa.evidence_geom import coerce_bbox

_MAX_CROSS_DUP_FINDINGS = 10


@dataclass(frozen=True)
class EnvelopeDocFeatures:
    """一份投标文档进入信封收敛的输入"""

    document_id: int
    bid_part: Optional[str]
    features: dict[str, Any] = field(default_factory=dict)


def _merge_slot_ledgers(
    docs: list[EnvelopeDocFeatures],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for doc in docs:
        ledger = (doc.features or {}).get("entity_slots") or {}
        for slot_key, entry in ledger.items():
            target = merged.setdefault(slot_key, {
                "label": entry.get("label") or slot_key,
                "severity": entry.get("severity") or "P1",
                "occurrences": [],
            })
            for occ in entry.get("occurrences", []):
                item = dict(occ)
                item.setdefault("document_id", doc.document_id)
                item.setdefault("bid_part", doc.bid_part)
                target["occurrences"].append(item)
    return merged


def _cross_duplicate_findings(docs: list[EnvelopeDocFeatures]) -> list[Finding]:
    """跨卷段落精确重复(商务/技术共用公司简介等属常见情况, 只给疑似提示)。"""
    by_hash: dict[str, list[tuple[EnvelopeDocFeatures, dict[str, Any]]]] = {}
    for doc in docs:
        hashes = (doc.features or {}).get("paragraph_hashes") or {}
        for digest, info in hashes.items():
            by_hash.setdefault(digest, []).append((doc, info))

    findings: list[Finding] = []
    for digest, hits in by_hash.items():
        doc_ids = {doc.document_id for doc, _ in hits}
        if len(doc_ids) < 2:
            continue
        (doc_a, info_a), (doc_b, info_b) = hits[0], hits[1]
        findings.append(Finding(
            rule_code="DOC-DUP-101",
            category=QaCategory.DUPLICATION,
            severity=QaSeverity.P3,
            certainty=QaCertainty.POSSIBLE,
            title="跨卷内容重复",
            message=(
                "同一段落同时出现在不同分卷中(公司简介/固定声明类内容跨卷复用属正常情况)"
            ),
            suggestion="确认是否为无意的跨卷内容复制",
            evidence=Evidence(
                kind="pair",
                items=(
                    EvidenceItem(
                        excerpt=str(info_a.get("excerpt") or ""),
                        page=info_a.get("page"),
                        document_id=doc_a.document_id,
                        bid_part=doc_a.bid_part,
                        bbox=coerce_bbox(info_a.get("bbox")),
                        role="primary",
                    ),
                    EvidenceItem(
                        excerpt=str(info_b.get("excerpt") or ""),
                        page=info_b.get("page"),
                        document_id=doc_b.document_id,
                        bid_part=doc_b.bid_part,
                        bbox=coerce_bbox(info_b.get("bbox")),
                        role="comparison",
                    ),
                ),
            ),
            anchor=f"cross_dup:{digest[:16]}",
        ))
        if len(findings) >= _MAX_CROSS_DUP_FINDINGS:
            break
    return findings


def analyze_envelope(docs: list[EnvelopeDocFeatures]) -> list[dict[str, Any]]:
    """跨卷收敛: 返回可持久化的发现项 payload 列表(单文档信封为空集)。"""
    from app.core.document_qa.checks.consistency_check import slot_conflict_findings

    findings: list[Finding] = []
    merged_ledger = _merge_slot_ledgers(docs)
    findings.extend(slot_conflict_findings(
        merged_ledger, rule_code="DOC-CONS-101", cross_document=True,
    ))
    findings.extend(_cross_duplicate_findings(docs))

    rows: list[dict[str, Any]] = []
    for finding in findings:
        data = finding.to_dict()
        data["document_id"] = None  # 跨卷发现不归属单一文档
        data["finding_fingerprint"] = finding.fingerprint(None)
        rows.append(data)
    return rows


def summarize_findings(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """发现项汇总统计(结果页概览与报告共用)。"""
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_certainty: dict[str, int] = {}
    for row in rows:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
        by_severity[row["severity"]] = by_severity.get(row["severity"], 0) + 1
        by_certainty[row["certainty"]] = by_certainty.get(row["certainty"], 0) + 1
    return {
        "total": len(rows),
        "by_category": by_category,
        "by_severity": by_severity,
        "by_certainty": by_certainty,
    }

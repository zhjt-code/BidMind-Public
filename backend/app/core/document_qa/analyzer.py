"""
职责: 单文档质检编排——按固定顺序运行检查器, 单点失败降级不中断,
      并产出跨卷收敛所需的紧凑特征
不做: 不构建 DocumentModel、不落库、不做跨文档比对
"""

from __future__ import annotations

from typing import Any, Callable

from loguru import logger

from app.core.document_qa.contracts import (
    CheckerOutcome,
    CheckerStatus,
    DocumentAnalysis,
    DocumentModel,
    Finding,
    normalize_for_hash,
    paragraph_hash,
)

_FEATURE_HASH_CAP = 5000
_FEATURE_MIN_PARAGRAPH_CHARS = 60


def _checker_registry() -> list[tuple[str, Callable[[DocumentModel, dict], CheckerOutcome]]]:
    """检查器注册表(固定顺序, 延迟导入避免加载开销)。"""
    from app.core.document_qa.checks.consistency_check import check_consistency
    from app.core.document_qa.checks.duplicate_check import check_duplicate
    from app.core.document_qa.checks.hygiene_check import check_hygiene
    from app.core.document_qa.checks.language_check import check_language
    from app.core.document_qa.checks.layout_check import check_layout
    from app.core.document_qa.checks.numbering_check import check_numbering
    from app.core.document_qa.checks.page_integrity_check import check_page_integrity
    from app.core.document_qa.checks.reference_check import check_reference
    from app.core.document_qa.checks.structure_check import check_structure
    from app.core.document_qa.checks.toc_check import check_toc

    return [
        ("structure", check_structure),
        ("toc", check_toc),
        ("layout", check_layout),
        ("numbering", check_numbering),
        ("reference", check_reference),
        ("hygiene", check_hygiene),
        ("page_integrity", check_page_integrity),
        ("duplicate", check_duplicate),
        ("consistency", check_consistency),
        ("language", check_language),
    ]


def _build_features(model: DocumentModel, rules: dict) -> dict[str, Any]:
    """跨卷收敛消费的紧凑特征(必须可 JSON 序列化)。"""
    from app.core.document_qa.checks.consistency_check import collect_slot_ledger
    from app.core.document_qa.evidence_geom import locate_bbox
    from app.core.document_qa.region_text import body_paragraphs

    ledger = collect_slot_ledger(model, rules)
    for entry in ledger.values():
        for occ in entry["occurrences"]:
            occ["document_id"] = model.document_id
            occ["bid_part"] = model.bid_part

    hashes: dict[str, dict[str, Any]] = {}
    paragraphs = body_paragraphs(model)
    for page_num in sorted(paragraphs):
        for paragraph in paragraphs[page_num]:
            if len(normalize_for_hash(paragraph)) < _FEATURE_MIN_PARAGRAPH_CHARS:
                continue
            digest = paragraph_hash(paragraph)
            if digest not in hashes:
                found = locate_bbox(model, page_num, paragraph[:120])
                hashes[digest] = {
                    "page": page_num,
                    "excerpt": paragraph[:120],
                    "bbox": list(found) if found else None,
                }
            if len(hashes) >= _FEATURE_HASH_CAP:
                break
        if len(hashes) >= _FEATURE_HASH_CAP:
            break

    return {
        "entity_slots": ledger,
        "paragraph_hashes": hashes,
        "page_count": model.page_count,
        "image_pages": [page.page_num for page in model.pages if page.image_only],
        "layout_pages": len(model.regions),
        "unknown_region_labels": dict(model.unknown_region_labels),
        "heading_count": len(model.headings),
        "style_profile": model.style_profile,
    }


def analyze_document(model: DocumentModel, rules: dict) -> DocumentAnalysis:
    """运行全部检查器并汇集发现项与特征。

    单检查器异常只降级(checker_status=failed), 禁止让一个检查器拖垮整次质检。
    """
    analysis = DocumentAnalysis()

    if model.degraded_reason:
        analysis.degraded = True

    for code, checker in _checker_registry():
        try:
            outcome = checker(model, rules)
            analysis.checker_status[code] = outcome.status.value
            analysis.findings.extend(outcome.findings)
            if outcome.status == CheckerStatus.FAILED:
                analysis.degraded = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[文档质检] 检查器 {code} 执行失败(降级): document={model.document_id}, {exc}",
            )
            analysis.checker_status[code] = CheckerStatus.FAILED.value
            analysis.degraded = True

    from app.core.document_qa.evidence_geom import enrich_findings

    enrich_findings(model, analysis.findings)

    try:
        analysis.features = _build_features(model, rules)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 特征构建失败(跨卷收敛将退化): {exc}")
        analysis.features = {}
        analysis.degraded = True

    return analysis


def findings_to_dicts(
    findings: list[Finding],
    *,
    document_id: int | None,
    bid_part: str | None,
) -> list[dict[str, Any]]:
    """把 Finding 序列化为持久化行 payload(证据项缺省定位补文档归属)。"""
    rows: list[dict[str, Any]] = []
    for finding in findings:
        data = finding.to_dict()
        for item in data["evidence"]["items"]:
            if item.get("document_id") is None:
                item["document_id"] = document_id
            if item.get("bid_part") is None:
                item["bid_part"] = bid_part
        data["document_id"] = document_id
        data["finding_fingerprint"] = finding.fingerprint(bid_part)
        rows.append(data)
    return rows

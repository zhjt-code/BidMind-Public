"""信封收敛: 整本/分卷同口径判定 + 指纹稳定性 + 跨卷冲突分析"""

from app.core.document_qa.envelope_analyzer import (
    EnvelopeDocFeatures,
    analyze_envelope,
    summarize_findings,
)
from app.core.tasks.process.document_qa_finalize import (
    EnvelopeQaDocFact,
    EnvelopeQaSnapshot,
    decide_envelope_finalize,
    docs_needing_qa_backfill,
    document_run_is_current,
)
from app.services.document_qa.fingerprint import document_run_fingerprint, envelope_fingerprint


def _fact(
    document_id,
    bid_part,
    *,
    parsed=1,
    run_status="completed",
    run_fingerprint=None,
    file_hash=None,
    content_hash=None,
):
    file_hash = file_hash if file_hash is not None else f"fh{document_id}"
    content_hash = content_hash if content_hash is not None else f"ch{document_id}"
    if run_fingerprint is None and run_status in ("completed", "degraded"):
        run_fingerprint = document_run_fingerprint(
            document_id=document_id,
            file_hash=file_hash,
            content_hash=content_hash,
        )
    return EnvelopeQaDocFact(
        document_id=document_id, bid_part=bid_part, parsed=parsed,
        file_hash=file_hash, content_hash=content_hash,
        run_status=run_status, run_fingerprint=run_fingerprint,
    )


def _snapshot(docs, *, project_type="bid_review", qa_enabled=True):
    return EnvelopeQaSnapshot(
        project_exists=True, project_type=project_type,
        qa_enabled=qa_enabled, docs=docs,
    )


# ── 判定纯函数: 整本与分卷共用同一链路 ─────────────────────────────────


def test_integral_envelope_ready():
    decision = decide_envelope_finalize(_snapshot([_fact(1, None)]))
    assert decision.should_finalize and decision.reason == "ready"


def test_split_envelope_ready():
    decision = decide_envelope_finalize(_snapshot([
        _fact(1, "commercial"), _fact(2, "technical"),
    ]))
    assert decision.should_finalize


def test_split_envelope_missing_part_not_finalized():
    decision = decide_envelope_finalize(_snapshot([_fact(1, "commercial")]))
    assert not decision.should_finalize
    assert decision.reason == "envelope_incomplete"


def test_unparsed_document_blocks_finalize():
    decision = decide_envelope_finalize(_snapshot([
        _fact(1, "commercial"), _fact(2, "technical", parsed=0),
    ]))
    assert decision.reason == "documents_not_parsed"


def test_missing_document_run_blocks_finalize():
    decision = decide_envelope_finalize(_snapshot([
        _fact(1, "commercial"), _fact(2, "technical", run_status=None),
    ]))
    assert decision.reason == "document_runs_not_ready"


def test_stale_ruleset_fingerprint_blocks_finalize():
    """规则集变化后, completed run 仍视为未就绪。"""
    stale = _fact(1, None, run_fingerprint="0" * 64)
    decision = decide_envelope_finalize(_snapshot([stale]))
    assert not decision.should_finalize
    assert decision.reason == "document_runs_not_ready"
    assert not document_run_is_current(stale)


def test_stale_content_hash_fingerprint_blocks_finalize():
    """同形态第二实体: 内容哈希变了, completed run 不算当前。"""
    old_fp = document_run_fingerprint(
        document_id=1, file_hash="fh1", content_hash="old",
    )
    stale = _fact(1, None, content_hash="new", run_fingerprint=old_fp)
    assert not document_run_is_current(stale)
    assert decide_envelope_finalize(_snapshot([stale])).reason == "document_runs_not_ready"


def test_backfill_targets_skip_current_include_stale_completed():
    current = _fact(1, "commercial")
    stale = _fact(2, "technical", run_fingerprint="0" * 64)
    targets = docs_needing_qa_backfill([current, stale])
    assert [doc.document_id for doc in targets] == [2]


def test_degraded_matching_fingerprint_is_current():
    doc = _fact(1, None, run_status="degraded")
    assert document_run_is_current(doc)
    assert decide_envelope_finalize(_snapshot([doc])).should_finalize


def test_non_bid_review_and_disabled_skipped():
    assert decide_envelope_finalize(
        _snapshot([_fact(1, None)], project_type="collusion_check")
    ).reason == "not_bid_review_project"
    assert decide_envelope_finalize(
        _snapshot([_fact(1, None)], qa_enabled=False)
    ).reason == "disabled"


# ── 信封指纹 ───────────────────────────────────────────────────────────


def test_envelope_fingerprint_order_independent():
    docs_a = [
        {"document_id": 2, "bid_part": "technical", "file_hash": "f2", "content_hash": "c2"},
        {"document_id": 1, "bid_part": "commercial", "file_hash": "f1", "content_hash": "c1"},
    ]
    docs_b = list(reversed(docs_a))
    assert envelope_fingerprint(docs_a) == envelope_fingerprint(docs_b)


def test_envelope_fingerprint_changes_with_content():
    base = [{"document_id": 1, "bid_part": None, "file_hash": "f1", "content_hash": "c1"}]
    changed = [{"document_id": 1, "bid_part": None, "file_hash": "f1", "content_hash": "c9"}]
    assert envelope_fingerprint(base) != envelope_fingerprint(changed)


# ── 跨卷收敛分析 ───────────────────────────────────────────────────────


def _slot_features(document_id, bid_part, value, normalized):
    return {
        "entity_slots": {
            "project_no": {
                "label": "项目编号",
                "severity": "P0",
                "occurrences": [{
                    "value": value, "normalized": normalized, "page": 3,
                    "excerpt": f"项目编号：{value}",
                    "document_id": document_id, "bid_part": bid_part,
                }],
            },
        },
        "paragraph_hashes": {},
    }


def test_cross_volume_slot_conflict():
    rows = analyze_envelope([
        EnvelopeDocFeatures(1, "commercial", _slot_features(1, "commercial", "ZB-2026-001", "ZB-2026-001")),
        EnvelopeDocFeatures(2, "technical", _slot_features(2, "technical", "ZB-2026-010", "ZB-2026-010")),
    ])
    conflicts = [r for r in rows if r["rule_code"] == "DOC-CONS-101"]
    assert len(conflicts) == 1
    items = conflicts[0]["evidence"]["items"]
    assert {item["bid_part"] for item in items} == {"commercial", "technical"}
    assert conflicts[0]["document_id"] is None  # 跨卷发现不归属单一文档


def test_cross_volume_same_value_no_conflict():
    rows = analyze_envelope([
        EnvelopeDocFeatures(1, "commercial", _slot_features(1, "commercial", "ZB-2026-001", "ZB-2026-001")),
        EnvelopeDocFeatures(2, "technical", _slot_features(2, "technical", "ZB—2026—001", "ZB-2026-001")),
    ])
    assert not [r for r in rows if r["rule_code"] == "DOC-CONS-101"]


def test_integral_envelope_cross_checks_empty():
    rows = analyze_envelope([
        EnvelopeDocFeatures(1, None, _slot_features(1, None, "A", "A")),
    ])
    assert rows == []


def test_summarize_counts():
    summary = summarize_findings([
        {"category": "toc", "severity": "P1", "certainty": "probable"},
        {"category": "toc", "severity": "P2", "certainty": "possible"},
    ])
    assert summary["total"] == 2
    assert summary["by_category"]["toc"] == 2
    assert summary["by_severity"] == {"P1": 1, "P2": 1}

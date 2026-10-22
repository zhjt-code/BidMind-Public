from types import SimpleNamespace


def _clause(metadata=None, *, sub_type="content_required"):
    return SimpleNamespace(
        sub_type=sub_type,
        metadata_json=metadata,
    )


def test_reference_relevance_is_not_counted_for_body_coverage() -> None:
    from app.core.writer.clause_scope_policy import is_body_coverage_counted

    clause = _clause({
        "writing_relevance": "reference",
        "writing_relevance_source": "matcher",
        "writing_relevance_reason": "与章节正文编写无关",
    })

    assert is_body_coverage_counted(clause) is False


def test_manual_relevance_override_keeps_clause_direct() -> None:
    from app.core.writer.clause_scope_policy import (
        WRITING_RELEVANCE_DIRECT,
        is_manual_relevance_override,
        read_writing_relevance,
    )

    clause = _clause({
        "writing_relevance": "reference",
        "writing_relevance_source": "manual",
        "writing_relevance_reason": "人工恢复为正文约束",
    })

    assert is_manual_relevance_override(clause) is True
    assert read_writing_relevance(clause) == WRITING_RELEVANCE_DIRECT


def test_build_relevance_mark_preserves_existing_metadata() -> None:
    from app.core.writer.clause_scope_policy import build_relevance_mark

    merged = build_relevance_mark(
        {"score_item_name": "技术方案"},
        level="reference",
        source="matcher",
        reason="与章节正文编写无关",
    )

    assert merged["score_item_name"] == "技术方案"
    assert merged["writing_relevance"] == "reference"
    assert merged["writing_relevance_source"] == "matcher"
    assert merged["writing_relevance_reason"] == "与章节正文编写无关"


def test_hidden_sub_type_still_uses_hidden_coverage_policy() -> None:
    from app.core.writer.clause_scope_policy import is_body_coverage_counted

    assert is_body_coverage_counted(_clause(sub_type="submission_rule")) is False

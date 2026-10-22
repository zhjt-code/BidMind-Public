from types import SimpleNamespace

import pytest


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    async def chat_json(self, **_kwargs):
        return self.payload


def _clause(clause_id: int, *, metadata=None):
    return SimpleNamespace(
        id=clause_id,
        sub_type="emphasis_point",
        layer="generation_guide",
        content="参考性写作提示",
        metadata_json=metadata,
    )


@pytest.mark.asyncio
async def test_irrelevant_scope_is_marked_reference_instead_of_silent_drop() -> None:
    from app.core.writer.material_matcher import MaterialMatcher

    clause = _clause(1)
    matcher = MaterialMatcher(
        db=SimpleNamespace(),
        llm_client=_FakeLLM([
            {"clause_id": 1, "scope": "irrelevant", "reason": "不影响正文编写"},
        ]),
    )

    global_clauses, target_clauses, scope_map = await matcher._classify_clauses([clause])

    assert global_clauses == []
    assert target_clauses == []
    assert scope_map[1]["scope"] == "irrelevant"
    assert clause.metadata_json["writing_relevance"] == "reference"
    assert clause.metadata_json["writing_relevance_source"] == "matcher"
    assert clause.metadata_json["writing_relevance_reason"] == "不影响正文编写"


@pytest.mark.asyncio
async def test_manual_reference_override_reenters_match_pool() -> None:
    from app.core.writer.material_matcher import MaterialMatcher

    clause = _clause(1, metadata={
        "writing_relevance": "reference",
        "writing_relevance_source": "manual",
    })
    matcher = MaterialMatcher(
        db=SimpleNamespace(),
        llm_client=_FakeLLM([
            {"clause_id": 1, "scope": "irrelevant", "reason": "AI误判"},
        ]),
    )

    _global_clauses, target_clauses, scope_map = await matcher._classify_clauses([clause])

    assert target_clauses == [clause]
    assert scope_map[1]["scope"] == "shared"


def test_unmatched_writing_guide_falls_back_to_global_binding() -> None:
    from app.core.writer.material_matcher import MaterialMatcher

    clause = _clause(10)
    matcher = MaterialMatcher(db=SimpleNamespace(), llm_client=_FakeLLM([]))

    bindings = matcher._consistency_check(
        all_bindings=[],
        global_clauses=[],
        target_clauses=[clause],
        scope_map={10: {"scope": "shared", "reason": "未命中章节"}},
    )

    assert len(bindings) == 1
    assert bindings[0]["clause_id"] == 10
    assert bindings[0]["scope"] == "global"
    assert bindings[0]["outline_node_id"] is None


def test_reference_writing_guide_does_not_fallback_to_global_binding() -> None:
    from app.core.writer.material_matcher import MaterialMatcher

    clause = _clause(11, metadata={
        "writing_relevance": "reference",
        "writing_relevance_source": "matcher",
    })
    matcher = MaterialMatcher(db=SimpleNamespace(), llm_client=_FakeLLM([]))

    bindings = matcher._consistency_check(
        all_bindings=[],
        global_clauses=[],
        target_clauses=[clause],
        scope_map={11: {"scope": "shared", "reason": "未命中章节"}},
    )

    assert len(bindings) == 1
    assert bindings[0]["clause_id"] == 11
    assert bindings[0]["scope"] == "orphan"

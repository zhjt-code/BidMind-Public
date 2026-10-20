from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.scoring


class _FakeLLM:
    def __init__(self, payload=None, *, raises: Exception | None = None):
        self.payload = payload if payload is not None else []
        self.raises = raises

    async def chat_json(self, **_kwargs):
        if self.raises:
            raise self.raises
        return self.payload


def _clause(clause_id: int, sub_type: str, content: str, *, metadata=None, raw_text=""):
    return SimpleNamespace(
        id=clause_id,
        sub_type=sub_type,
        content=content,
        raw_text=raw_text,
        metadata_json=metadata,
    )


@pytest.mark.asyncio
async def test_scoring_and_substantive_clauses_are_never_demoted() -> None:
    from app.core.writer.writing_relevance.classifier import classify_writing_relevance

    clauses = [
        _clause(1, "scoring_criterion", "技术方案10分"),
        _clause(2, "disqualification", "★ 未响应实质性条款废标", raw_text="★ 未响应实质性条款废标"),
    ]

    await classify_writing_relevance(
        clauses,
        llm_client=_FakeLLM([
            {"clause_id": 1, "writing_relevance": "reference", "reason": "LLM误判"},
            {"clause_id": 2, "writing_relevance": "reference", "reason": "LLM误判"},
        ]),
    )

    assert clauses[0].metadata_json["writing_relevance"] == "direct"
    assert clauses[1].metadata_json["writing_relevance"] == "direct"


@pytest.mark.asyncio
async def test_manual_override_is_not_changed_by_classifier() -> None:
    from app.core.writer.writing_relevance.classifier import classify_writing_relevance

    clause = _clause(1, "emphasis_point", "参考提示", metadata={
        "writing_relevance": "direct",
        "writing_relevance_source": "manual",
        "writing_relevance_reason": "人工恢复",
    })

    await classify_writing_relevance(
        [clause],
        llm_client=_FakeLLM([
            {"clause_id": 1, "writing_relevance": "reference", "reason": "LLM误判"},
        ]),
    )

    assert clause.metadata_json["writing_relevance"] == "direct"
    assert clause.metadata_json["writing_relevance_source"] == "manual"


@pytest.mark.asyncio
async def test_rule_floor_marks_procedural_notice_reference() -> None:
    from app.core.writer.writing_relevance.classifier import classify_writing_relevance

    clause = _clause(1, "project_context", "开标时间为上午9点，地点为交易中心")

    await classify_writing_relevance([clause], llm_client=_FakeLLM([]))

    assert clause.metadata_json["writing_relevance"] == "reference"
    assert clause.metadata_json["writing_relevance_source"] == "extract_pass"
    assert "程序" in clause.metadata_json["writing_relevance_reason"]


@pytest.mark.asyncio
async def test_llm_failure_keeps_default_direct() -> None:
    from app.core.writer.writing_relevance.classifier import classify_writing_relevance

    clause = _clause(1, "emphasis_point", "招标人关注服务响应质量")

    await classify_writing_relevance(
        [clause],
        llm_client=_FakeLLM(raises=RuntimeError("llm down")),
    )

    assert clause.metadata_json["writing_relevance"] == "direct"
    assert clause.metadata_json["writing_relevance_source"] == "extract_pass"


@pytest.mark.asyncio
async def test_signature_disqualification_is_check_only() -> None:
    from app.core.writer.writing_relevance.classifier import classify_writing_relevance

    clause = _clause(1, "disqualification", "投标文件未按要求逐页签字盖章将按无效投标处理")

    await classify_writing_relevance([clause], llm_client=_FakeLLM([]))

    assert clause.metadata_json["writing_relevance"] == "check_only"
    assert clause.metadata_json["writing_relevance_source"] == "extract_pass"
    assert "一致性检查" in clause.metadata_json["writing_relevance_reason"]


@pytest.mark.asyncio
async def test_duplicate_disqualification_is_folded_as_reference() -> None:
    from app.core.writer.writing_relevance.classifier import classify_writing_relevance

    first = _clause(1, "disqualification", "未按要求提供授权委托书将按无效投标处理")
    duplicate = _clause(2, "disqualification", "未按要求提供授权委托书将按无效投标处理")

    await classify_writing_relevance([first, duplicate], llm_client=_FakeLLM([]))

    assert first.metadata_json["writing_relevance"] == "direct"
    assert duplicate.metadata_json["writing_relevance"] == "reference"
    assert duplicate.metadata_json["duplicate_of_clause_id"] == 1

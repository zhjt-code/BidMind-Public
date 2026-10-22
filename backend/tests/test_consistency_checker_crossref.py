"""B2.3 — _check_cross_references 静默失效修复验证。

LLM 异常时必须返回 degraded issue 并 inc 指标，而非静默 return []。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.contracts.failure_state import FailureState


@pytest.fixture()
def _mock_db():
    return AsyncMock()


def _make_checker(mock_db):
    """构造 ConsistencyChecker，注入 mock LLM client。"""
    from app.core.writer.consistency_checker import ConsistencyChecker

    llm = AsyncMock()
    checker = ConsistencyChecker(db=mock_db, llm_client=llm)
    return checker, llm


def test_cross_ref_returns_degraded_on_llm_error(_mock_db):
    """LLM 抛异常 → 返回 1 条 degraded issue 且指标 +1。"""
    checker, llm = _make_checker(_mock_db)

    llm.chat_json = AsyncMock(side_effect=RuntimeError("provider timeout"))

    from app.models.writing_outline_node import WritingOutlineNode
    from app.models.writing_section_draft import WritingSectionDraft

    draft = MagicMock(spec=WritingSectionDraft)
    draft.content = "本投标方案详见第三章技术方案章"
    draft.outline_node_id = 1

    node = MagicMock(spec=WritingOutlineNode)
    node.id = 1
    node.title = "第一章 综述"

    drafts = {1: draft}
    nodes = [node]

    with patch(
        "app.core.writer.consistency_checker.cross_reference_check_failure_total"
    ) as mock_counter:
        mock_labels = MagicMock()
        mock_counter.labels.return_value = mock_labels

        issues = asyncio.get_event_loop().run_until_complete(
            checker._check_cross_references(drafts, nodes)
        )

    assert len(issues) == 1
    issue = issues[0]
    assert issue["type"] == "cross_reference_check_degraded"
    assert issue["failure_state"] == FailureState.DEGRADED.value
    assert issue["failure_reason"] == "cross_reference_llm_network_error"
    mock_counter.labels.assert_called_once_with(
        flow="writer", reason="RuntimeError"
    )
    mock_labels.inc.assert_called_once()


def test_cross_ref_normal_path_unchanged(_mock_db):
    """正常路径返回 issue 列表不受影响。"""
    checker, llm = _make_checker(_mock_db)

    llm.chat_json = AsyncMock(return_value=[
        {"fragment": "第三章", "is_correct": False, "suggestion": "应为第四章"},
    ])

    draft = MagicMock()
    draft.content = "详见第三章技术方案章节的内容"
    draft.outline_node_id = 1

    node = MagicMock()
    node.id = 1
    node.title = "第一章"

    issues = asyncio.get_event_loop().run_until_complete(
        checker._check_cross_references({1: draft}, [node])
    )

    assert len(issues) == 1
    assert issues[0]["type"] == "cross_reference_error"
    user_message = llm.chat_json.await_args.kwargs["user_message"]
    assert "## 待检查的引用记录" in user_message
    assert "source_section:" in user_message
    assert "context:" in user_message

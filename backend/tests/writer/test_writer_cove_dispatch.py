""" 防御测试：SectionComposer 写标 CoVe 主链路接入。

锁定的契约：
1. ``_is_writer_critical_commitment`` 命中关键词章节标题时返回
 (True, keyword)；非关键章节返回 (False, "关键承诺")。
2. ``_maybe_run_writer_cove`` 在 ``COVE_ENABLED=False`` /
 ``should_use_cove=False`` / 非关键章节 / judge client 缺失等场景下
 静默返回 None，不调用 ``run_cove``。
3. 关键承诺章节命中时调用 ``run_cove(trigger="writer_commitment",
 flow="writer")``，evidence_snippets 含正文与素材片段。
4. CoVe disagree 触发时 ``_maybe_run_writer_cove`` 返回 ``WriterCoVe``
 gate_flags 条目，含 ``failure_state=needs_review`` patch。
5. ``_merge_gate_flags`` 同时保留 Gate 与 CoVe 条目。

仅测纯函数 + asyncio 入口，不依赖 DB / Dispatcher。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.ai.critic.cove import CoveAnswer, CoveResult
from app.core.writer.section_composer import (
    ComposePayload,
    SectionComposer,
)


@dataclass
class _FakeNode:
    id: int = 1
    title: str = ""
    node_type: Any = None
    parent_id: int | None = None
    sort_order: int = 0


def _payload(content: str = "", *, materials: list[int] | None = None) -> ComposePayload:
    return ComposePayload(
        content=content,
        source_material_ids=list(materials or []),
        source_clause_ids=[],
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. _is_writer_critical_commitment 纯函数
# ---------------------------------------------------------------------------


class TestIsWriterCriticalCommitment:

    def test_title_hit_returns_keyword(self):
        node = _FakeNode(title="第3章 资质承诺函")
        is_crit, kw = SectionComposer._is_writer_critical_commitment(
            node, _payload("正文 ..."),
        )
        assert is_crit is True
        # 命中关键词集合中的某一个（按枚举顺序优先）
        assert kw in {"资质", "承诺"}

    def test_non_critical_title_without_commitment_in_body(self):
        node = _FakeNode(title="第1章 公司简介")
        is_crit, kw = SectionComposer._is_writer_critical_commitment(
            node, _payload("我司成立于 2010 年。"),
        )
        assert is_crit is False
        assert kw == "关键承诺"

    def test_body_commitment_with_amount_lifts_to_critical(self):
        node = _FakeNode(title="第5章 服务方案")
        body = "本项目我司承诺工期 30 天内完工，并提供 ¥100 万元保证金。"
        is_crit, kw = SectionComposer._is_writer_critical_commitment(
            node, _payload(body),
        )
        assert is_crit is True
        assert kw  # 命中具体承诺关键词

    def test_body_commitment_without_amount_stays_non_critical(self):
        node = _FakeNode(title="第5章 服务方案")
        body = "我司承诺保质保量提供服务。"
        is_crit, _ = SectionComposer._is_writer_critical_commitment(
            node, _payload(body),
        )
        assert is_crit is False

    def test_empty_payload_returns_false(self):
        node = _FakeNode(title="普通章节")
        assert SectionComposer._is_writer_critical_commitment(
            node, _payload(""),
        ) == (False, "关键承诺")


# ---------------------------------------------------------------------------
# 2. _merge_gate_flags 纯函数
# ---------------------------------------------------------------------------


class TestMergeGateFlags:

    def test_both_none_returns_none(self):
        assert SectionComposer._merge_gate_flags(None, None) is None

    def test_only_base(self):
        base = {"GateA": {"verdict": "warn"}}
        assert SectionComposer._merge_gate_flags(base, None) == base

    def test_only_extra(self):
        extra = {"WriterCoVe": {"verdict": "disagree"}}
        assert SectionComposer._merge_gate_flags(None, extra) == extra

    def test_merges_distinct_keys(self):
        base = {"GateA": {"verdict": "warn"}}
        extra = {"WriterCoVe": {"verdict": "disagree"}}
        merged = SectionComposer._merge_gate_flags(base, extra)
        assert merged is not None
        assert set(merged.keys()) == {"GateA", "WriterCoVe"}


# ---------------------------------------------------------------------------
# 3. _maybe_run_writer_cove 调用链
# ---------------------------------------------------------------------------


def _make_composer() -> SectionComposer:
    """构造一个最小可用的 SectionComposer 实例，绕过 DB 依赖。"""
    composer = SectionComposer.__new__(SectionComposer)
    composer.db = None  # type: ignore[attr-defined]
    composer._material_pool_size_cache = {}  # type: ignore[attr-defined]
    return composer


def _evidence_stub() -> dict:
    return {
        "bound_materials": [
            {"title": "公司业绩", "snippet": "2024 年完成 ¥500 万项目"},
        ],
        "hard_constraints": ["禁止转包"],
    }


def test_maybe_run_writer_cove_skips_when_should_use_cove_false():
    composer = _make_composer()
    node = _FakeNode(title="资质承诺")
    payload = _payload("我司承诺工期 30 天，¥100 万保证金。")

    with patch(
        "app.core.ai.critic.cove.should_use_cove", return_value=False,
    ), patch(
        "app.core.ai.critic.cove.run_cove",
    ) as mock_run_cove:
        result = _run(composer._maybe_run_writer_cove(123, node, payload))

    assert result is None
    mock_run_cove.assert_not_called()


def test_maybe_run_writer_cove_skips_when_node_not_critical():
    composer = _make_composer()
    node = _FakeNode(title="公司简介")
    payload = _payload("我司成立于 2010 年。")

    with patch(
        "app.core.ai.critic.cove.should_use_cove", return_value=True,
    ), patch(
        "app.core.ai.critic.cove.run_cove",
    ) as mock_run_cove:
        result = _run(composer._maybe_run_writer_cove(123, node, payload))

    assert result is None
    mock_run_cove.assert_not_called()


def test_maybe_run_writer_cove_skips_when_judge_client_missing():
    composer = _make_composer()
    node = _FakeNode(title="资质承诺")
    payload = _payload("我司承诺工期 30 天，¥100 万保证金。")

    with patch(
        "app.core.ai.critic.cove.should_use_cove", return_value=True,
    ), patch(
        "app.core.ai.ai_factory.get_judge_client", return_value=None,
    ), patch(
        "app.core.ai.critic.cove.run_cove",
    ) as mock_run_cove:
        result = _run(composer._maybe_run_writer_cove(123, node, payload))

    assert result is None
    mock_run_cove.assert_not_called()


def test_maybe_run_writer_cove_invokes_run_cove_with_writer_trigger():
    """关键承诺章节必须调用 run_cove(trigger='writer_commitment', flow='writer')，
    且 evidence_snippets 含正文与素材片段。"""
    composer = _make_composer()
    node = _FakeNode(title="承诺函")
    body = "我司承诺工期 30 天内完工，¥100 万元保证金。"
    payload = _payload(body, materials=[1])

    captured: dict[str, Any] = {}

    async def fake_run_cove(**kwargs):
        captured.update(kwargs)
        return CoveResult(
            trigger="writer_commitment",
            outcome="agree",
            answers=[],
            should_escalate=False,
        )

    fake_evidence_builder = AsyncMock(return_value=_evidence_stub())
    composer._build_writer_judge_evidence = fake_evidence_builder  # type: ignore[attr-defined]

    with patch(
        "app.core.ai.critic.cove.should_use_cove", return_value=True,
    ), patch(
        "app.core.ai.ai_factory.get_judge_client", return_value=object(),
    ), patch(
        "app.core.ai.critic.cove.run_cove", side_effect=fake_run_cove,
    ):
        result = _run(composer._maybe_run_writer_cove(123, node, payload))

    # outcome=agree 时不写 gate_flags（视为静默通过）
    assert result is None
    assert captured["trigger"] == "writer_commitment"
    assert captured["flow"] == "writer"
    snippets = captured["evidence_snippets"]
    assert any(body[:200] in s["text"] for s in snippets), (
        f"chapter body not embedded in evidence snippets: {snippets}"
    )
    assert any("2024 年完成" in s["text"] for s in snippets), (
        "bound material snippet must be in evidence_snippets"
    )
    assert any("禁止转包" in s["text"] for s in snippets), (
        "hard_constraint must be in evidence_snippets"
    )


def test_maybe_run_writer_cove_disagree_returns_needs_review_flag():
    composer = _make_composer()
    node = _FakeNode(title="资质承诺")
    body = "我司承诺工期 30 天，¥100 万保证金。"
    payload = _payload(body)

    composer._build_writer_judge_evidence = AsyncMock(  # type: ignore[attr-defined]
        return_value={"bound_materials": [], "hard_constraints": []},
    )

    async def fake_run_cove(**kwargs):
        return CoveResult(
            trigger="writer_commitment",
            outcome="disagree",
            answers=[
                CoveAnswer(
                    question_id="wrt_q1", answer="no",
                    evidence_page=0, quote="x", valid=True,
                ),
            ],
            should_escalate=True,
            reason="",
        )

    with patch(
        "app.core.ai.critic.cove.should_use_cove", return_value=True,
    ), patch(
        "app.core.ai.ai_factory.get_judge_client", return_value=object(),
    ), patch(
        "app.core.ai.critic.cove.run_cove", side_effect=fake_run_cove,
    ):
        result = _run(composer._maybe_run_writer_cove(123, node, payload))

    assert result is not None
    assert "WriterCoVe" in result
    entry = result["WriterCoVe"]
    assert entry["verdict"] == "disagree"
    assert entry["severity"] == "advisory"
    assert entry["patch"] == {"failure_state": "needs_review"}
    assert entry["trace"]["trigger"] == "writer_commitment"


def test_maybe_run_writer_cove_inconclusive_returns_flag_without_patch():
    composer = _make_composer()
    node = _FakeNode(title="承诺函")
    payload = _payload("我司承诺工期 30 天，¥100 万保证金。")

    composer._build_writer_judge_evidence = AsyncMock(  # type: ignore[attr-defined]
        return_value={"bound_materials": [], "hard_constraints": []},
    )

    async def fake_run_cove(**kwargs):
        return CoveResult(
            trigger="writer_commitment",
            outcome="inconclusive",
            answers=[],
            should_escalate=False,
            reason="tie_vote",
        )

    with patch(
        "app.core.ai.critic.cove.should_use_cove", return_value=True,
    ), patch(
        "app.core.ai.ai_factory.get_judge_client", return_value=object(),
    ), patch(
        "app.core.ai.critic.cove.run_cove", side_effect=fake_run_cove,
    ):
        result = _run(composer._maybe_run_writer_cove(123, node, payload))

    assert result is not None
    entry = result["WriterCoVe"]
    assert entry["verdict"] == "inconclusive"
    assert entry["patch"] == {}
    assert "tie_vote" in entry["reason"]


def test_maybe_run_writer_cove_failsafe_on_exception():
    composer = _make_composer()
    node = _FakeNode(title="资质承诺")
    payload = _payload("我司承诺工期 30 天，¥100 万保证金。")

    composer._build_writer_judge_evidence = AsyncMock(  # type: ignore[attr-defined]
        side_effect=RuntimeError("simulated db failure"),
    )

    with patch(
        "app.core.ai.critic.cove.should_use_cove", return_value=True,
    ), patch(
        "app.core.ai.ai_factory.get_judge_client", return_value=object(),
    ), patch(
        "app.core.ai.critic.cove.run_cove",
    ) as mock_run_cove:
        result = _run(composer._maybe_run_writer_cove(123, node, payload))

    assert result is None
    mock_run_cove.assert_not_called()

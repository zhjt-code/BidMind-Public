"""写标约束单次过正文提取（方案 A /）。

职责: 锁定「N 个批次只打 N 次 LLM、三层从同一次 JSON 拆出、共用一把 Semaphore」。
不做: 不测去重、不测落库、不打真实模型。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.writer.clause_layer_pass import (
    ClauseLayerPassRequest,
    extract_clause_layers_from_batches,
)
from app.core.writer.prompts.clause_extract_unified import (
    UNIFIED_CLAUSE_EXTRACT_SYSTEM,
    UNIFIED_CLAUSE_EXTRACT_USER,
    get_unified_clause_extract_prompt,
)
from app.schemas.writing import ExtractedClause


class _FakeJsonClient:
    def __init__(self, result, delay_s: float = 0.0):
        self.result = result
        self.delay_s = delay_s
        self.calls: list[dict] = []
        self.max_inflight = 0
        self._inflight = 0

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if callable(self.result):
                return self.result(kwargs)
            return self.result
        finally:
            self._inflight -= 1


def _chunk(text: str, page: int) -> SimpleNamespace:
    return SimpleNamespace(content=text, page_num=page, chunk_index=page)


def _identity_verify(clauses, _text, _start, _end):
    return clauses


def _base_request(**overrides) -> ClauseLayerPassRequest:
    payload = {
        "batches": [[_chunk("须提供营业执照扫描件。", 1)]],
        "all_chunks": None,
        "llm_client": _FakeJsonClient({"hard": [], "structure": [], "guide": []}),
        "concurrency": 2,
        "report_progress": None,
        "verify_batch_clauses": _identity_verify,
        "detect_section_context": None,
        "check_text_quality": None,
        "build_prompt": lambda content, start, end: ("SYS", content),
    }
    payload.update(overrides)
    return ClauseLayerPassRequest(**payload)


@pytest.mark.asyncio
async def test_n_batches_make_n_llm_calls_not_three_n() -> None:
    """根因护栏: 4 批不得再打成 12 次（旧三层 gather）。"""
    batches = [[_chunk(f"批次{i}", i)] for i in range(1, 5)]
    client = _FakeJsonClient({"hard": [], "structure": [], "guide": []})
    result = await extract_clause_layers_from_batches(
        _base_request(batches=batches, llm_client=client)
    )
    assert len(client.calls) == 4
    assert result.hard == []
    assert result.structure == []
    assert result.guide == []


@pytest.mark.asyncio
async def test_one_response_splits_into_three_layers() -> None:
    client = _FakeJsonClient(
        {
            "hard": [
                {
                    "sub_type": "disqualification",
                    "content": "未响应★条款按废标处理",
                    "raw_text": "★条款未响应按废标",
                    "source_page": 2,
                }
            ],
            "structure": [
                {
                    "sub_type": "material_required",
                    "content": "提供营业执照扫描件",
                    "raw_text": "须提供营业执照扫描件",
                    "source_page": 2,
                }
            ],
            "guide": [
                {
                    "sub_type": "scoring_criterion",
                    "content": "技术方案完整性最高得5分",
                    "raw_text": "技术方案完整性：最高得5分",
                    "source_page": 2,
                    "score_weight": 5,
                }
            ],
        }
    )
    result = await extract_clause_layers_from_batches(
        _base_request(
            batches=[[_chunk("★条款未响应按废标。须提供营业执照扫描件。技术方案完整性：最高得5分", 2)]],
            llm_client=client,
        )
    )
    assert [c.sub_type for c in result.hard] == ["disqualification"]
    assert [c.sub_type for c in result.structure] == ["material_required"]
    assert [c.sub_type for c in result.guide] == ["scoring_criterion"]
    assert isinstance(result.hard[0], ExtractedClause)


@pytest.mark.asyncio
async def test_shared_semaphore_caps_inflight_across_all_batches() -> None:
    batches = [[_chunk(f"x{i}", i)] for i in range(6)]
    client = _FakeJsonClient(
        {"hard": [], "structure": [], "guide": []},
        delay_s=0.05,
    )
    await extract_clause_layers_from_batches(
        _base_request(batches=batches, llm_client=client, concurrency=2)
    )
    assert client.max_inflight <= 2
    assert len(client.calls) == 6


@pytest.mark.asyncio
async def test_progress_reports_batch_count_not_times_three() -> None:
    events: list[tuple[str, int, int]] = []

    def _progress(stage: str, current: int, total: int, message: str = "") -> None:
        events.append((stage, current, total))

    batches = [[_chunk("a", 1)], [_chunk("b", 2)], [_chunk("c", 3)]]
    await extract_clause_layers_from_batches(
        _base_request(batches=batches, report_progress=_progress)
    )
    assert events[-1] == ("extracting_clauses", 3, 3)
    assert all(total == 3 for _stage, _current, total in events)


@pytest.mark.asyncio
async def test_prompt_format_error_is_not_swallowed() -> None:
    def _bad_prompt(_content: str, _start: int, _end: int):
        raise KeyError("score_item_name")

    with pytest.raises(KeyError):
        await extract_clause_layers_from_batches(
            _base_request(build_prompt=_bad_prompt)
        )


@pytest.mark.asyncio
async def test_batch_llm_failure_returns_empty_for_that_batch() -> None:
    class _Boom:
        async def chat_json(self, **kwargs):
            raise RuntimeError("llm down")

    result = await extract_clause_layers_from_batches(
        _base_request(llm_client=_Boom())
    )
    assert result.hard == []
    assert result.structure == []
    assert result.guide == []


@pytest.mark.asyncio
async def test_list_response_is_rejected_not_guessed_into_a_layer() -> None:
    client = _FakeJsonClient(
        [
            {
                "sub_type": "disqualification",
                "content": "未提供指定材料废标",
                "raw_text": "未提供指定材料废标",
                "source_page": 1,
            }
        ]
    )
    result = await extract_clause_layers_from_batches(
        _base_request(llm_client=client)
    )
    assert result.hard == []
    assert result.structure == []
    assert result.guide == []


def test_unified_prompt_keeps_layer_mutex_and_object_schema() -> None:
    assert "只归入一层" in UNIFIED_CLAUSE_EXTRACT_SYSTEM or "只出现在" in UNIFIED_CLAUSE_EXTRACT_SYSTEM
    assert '"hard"' in UNIFIED_CLAUSE_EXTRACT_USER
    assert '"structure"' in UNIFIED_CLAUSE_EXTRACT_USER
    assert '"guide"' in UNIFIED_CLAUSE_EXTRACT_USER
    assert "material_required" in UNIFIED_CLAUSE_EXTRACT_SYSTEM
    assert "串通投标" in UNIFIED_CLAUSE_EXTRACT_SYSTEM


def test_unified_prompt_factory_formats_without_keyerror() -> None:
    system, user = get_unified_clause_extract_prompt("招标正文示例", 1, 3)
    assert "招标正文示例" in user
    assert "1" in user and "3" in user
    assert isinstance(system, str) and system.strip()

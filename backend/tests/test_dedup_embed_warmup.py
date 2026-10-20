""": 反思 LLM 与候选向量预热并行，异常路径回收预热任务。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings
from app.core.extractor.tender_extractor_parts import dedup_stage
from app.schemas.requirement import ExtractedRequirement


def _req(description: str) -> ExtractedRequirement:
    return ExtractedRequirement(
        category="COMM",
        description=description,
        is_mandatory=True,
        keywords=["保证金"],
    )


class _StubExtractor:
    def __init__(self, llm_client: object | None = None) -> None:
        self.llm_client = llm_client
        self.progress: list[str] = []

    def _check_quality(self, requirements: list[ExtractedRequirement]) -> list[dict]:
        return [{"type": "keyword_excess", "affected_indices": list(range(len(requirements)))}]

    def _report_progress(self, *_args: object, **kwargs: object) -> None:
        message = _args[3] if len(_args) > 3 else kwargs.get("message")
        if isinstance(message, str):
            self.progress.append(message)

    def _sanitize_requirements_for_prompt(
        self,
        requirements: list[ExtractedRequirement],
        merge_mode: bool = False,
    ) -> list[dict]:
        return [{"description": item.description} for item in requirements]

    def _is_truncation_error(self, _error: Exception) -> bool:
        return False

    def _is_json_like_error(self, _error: Exception) -> bool:
        return False

    def _record_stage_failure(self, *_args: object, **_kwargs: object) -> None:
        return None

    def _record_fallback(self, *_args: object, **_kwargs: object) -> None:
        return None

    def _merge_issue_lists(self, local_issues: list[dict], llm_issues: list[dict]) -> list[dict]:
        return list(local_issues) + list(llm_issues)

    async def _secondary_merge(
        self,
        requirements: list[ExtractedRequirement],
        _issues: list[dict],
    ) -> list[ExtractedRequirement]:
        return requirements


@pytest.mark.asyncio
async def test_embed_warmup_starts_before_reflection_returns(monkeypatch) -> None:
    order: list[str] = []

    async def fake_prefetch(texts: list[str]):
        order.append("encode_start")
        await asyncio.sleep(0.05)
        order.append("encode_end")
        return None

    class _LLM:
        async def chat_json(self, **_kwargs: object) -> dict:
            order.append("llm_start")
            await asyncio.sleep(0.05)
            order.append("llm_end")
            return {"has_issues": False, "quality_score": 100, "issues": []}

    monkeypatch.setattr(settings, "TENDER_CANDIDATE_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "TENDER_REFLECTION_JSON_RETRY", 0)
    monkeypatch.setattr(dedup_stage, "prefetch_candidate_embeddings", fake_prefetch)

    stub = _StubExtractor(llm_client=_LLM())
    reqs = [
        _req("投标保证金应按要求提交并从基本账户转出"),
        _req("投标保证金须从基本账户转出并提交凭证"),
    ]
    result = await dedup_stage.cross_category_dedup(stub, reqs)

    assert len(result) == 2
    assert order.index("encode_start") < order.index("llm_end")
    assert order.index("llm_start") < order.index("encode_end")


@pytest.mark.asyncio
async def test_embed_warmup_cancelled_when_reflection_setup_raises(monkeypatch) -> None:
    async def hanging_prefetch(_texts: list[str]):
        await asyncio.Event().wait()

    class _Boom(_StubExtractor):
        def _sanitize_requirements_for_prompt(
            self,
            requirements: list[ExtractedRequirement],
            merge_mode: bool = False,
        ) -> list[dict]:
            raise RuntimeError("sanitize fail")

    monkeypatch.setattr(settings, "TENDER_CANDIDATE_GATE_ENABLED", True)
    monkeypatch.setattr(dedup_stage, "prefetch_candidate_embeddings", hanging_prefetch)

    stub = _Boom()
    reqs = [
        _req("投标保证金应按要求提交并从基本账户转出"),
        _req("投标保证金须从基本账户转出并提交凭证"),
    ]
    with pytest.raises(RuntimeError, match="sanitize fail"):
        await dedup_stage.cross_category_dedup(stub, reqs)

    leftover = [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name() == "tender-candidate-embed-warmup"
    ]
    assert leftover == []


@pytest.mark.asyncio
async def test_embed_warmup_emits_heartbeat_when_still_running(monkeypatch) -> None:
    llm_done = asyncio.Event()

    async def slow_prefetch(_texts: list[str]):
        await llm_done.wait()
        await asyncio.sleep(0.01)
        return None

    class _LLM:
        async def chat_json(self, **_kwargs: object) -> dict:
            llm_done.set()
            return {"has_issues": False, "quality_score": 100, "issues": []}

    monkeypatch.setattr(settings, "TENDER_CANDIDATE_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "TENDER_REFLECTION_JSON_RETRY", 0)
    monkeypatch.setattr(dedup_stage, "prefetch_candidate_embeddings", slow_prefetch)

    stub = _StubExtractor(llm_client=_LLM())
    reqs = [
        _req("投标保证金应按要求提交并从基本账户转出"),
        _req("投标保证金须从基本账户转出并提交凭证"),
    ]
    await dedup_stage.cross_category_dedup(stub, reqs)

    assert "候选语义压缩中" in stub.progress
    assert "反思链路完成，执行精度保护合并" in stub.progress

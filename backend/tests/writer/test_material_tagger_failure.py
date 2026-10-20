"""P0.3 防御测试：MaterialTagger 失败可见化 + tag_batch 受控并发。

用 AsyncMock 代替真实 LLMClient / DB session, 聚焦行为断言。
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.material_tagger import MaterialTagger


def _make_material(
    mid: int = 1,
    title: str = "公司简介.pdf",
    content: str = "本公司成立于 2005 年, 主要从事 XX 业务",
    material_type: str = "text",
    label_conflict: str | None = None,
    resolved_label: str | None = None,
):
    mat = MagicMock()
    mat.id = mid
    mat.title = title
    mat.display_title = None
    mat.content = content
    mat.material_type = material_type
    mat.metadata_tags = None
    mat.label_conflict = label_conflict
    mat.resolved_label = resolved_label
    mat.structure_hint_label = None
    mat.file_name_label = None
    return mat


@pytest.fixture
def tagger_factory():
    def _make(llm_client: AsyncMock):
        db = AsyncMock()
        db.commit = AsyncMock()
        tagger = MaterialTagger.__new__(MaterialTagger)
        tagger.db = db
        tagger.llm_client = llm_client
        from app.core.ai.retry_tracker import LLMRetryTracker

        tagger._retry_tracker = LLMRetryTracker("material_tagger_test")
        return tagger

    return _make


class TestTagFailureVisibility:
    """tag() LLM 失败时, metadata_tags 中 _tag_status 必须标记为 llm_failed。"""

    @pytest.mark.asyncio
    async def test_llm_failure_marks_tag_status_llm_failed(self, tagger_factory):
        llm = AsyncMock()
        llm.chat_json = AsyncMock(side_effect=RuntimeError("provider timeout"))
        tagger = tagger_factory(llm)

        mat = _make_material(mid=11, title="业绩综述.pdf", content="历年代表项目")
        result = await tagger.tag(mat)

        assert result is not None
        assert result.get("_tag_status") == "llm_failed"
        # 持久化侧: metadata_tags JSON 内应该含 _tag_status
        assert mat.metadata_tags
        persisted = json.loads(mat.metadata_tags)
        assert persisted.get("_tag_status") == "llm_failed"

    @pytest.mark.asyncio
    async def test_llm_success_marks_tag_status_success(self, tagger_factory):
        llm = AsyncMock()
        llm.chat_json = AsyncMock(return_value={
            "domain": "项目业绩",
            "entities": ["X项目"],
            "keywords": ["业绩"],
            "applicable_sections": ["项目经验"],
        })
        tagger = tagger_factory(llm)

        mat = _make_material(mid=12)
        result = await tagger.tag(mat)

        assert result.get("_tag_status") == "llm_success"

    @pytest.mark.asyncio
    async def test_image_skipped_marks_image_skipped(self, tagger_factory):
        llm = AsyncMock()
        # 应该不会调到 LLM
        llm.chat_json = AsyncMock(side_effect=AssertionError("should not be called"))
        tagger = tagger_factory(llm)

        mat = _make_material(
            mid=13, material_type="image", label_conflict="uncertain",
        )
        result = await tagger.tag(mat)

        assert result.get("_tag_status") == "image_skipped"
        llm.chat_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_path_does_not_overwrite_status(self, tagger_factory):
        """_is_retry=True 路径: 失败时返回 None, 不再污染 material。"""
        llm = AsyncMock()
        llm.chat_json = AsyncMock(side_effect=RuntimeError("still down"))
        tagger = tagger_factory(llm)

        mat = _make_material(mid=14)
        result = await tagger.tag(mat, _is_retry=True)
        assert result is None


class TestTagBatchConcurrency:
    """tag_batch() 应使用 Semaphore 控制并发, 并在末尾自动调用 retry_failed。"""

    @pytest.mark.asyncio
    async def test_concurrency_bounded_to_semaphore(self, tagger_factory, monkeypatch):
        """并发上限 = _MAX_TAG_CONCURRENCY, 观察者不应看到同时 >上限 个 tag 调用。"""
        from app.core.writer import material_tagger as mt_mod

        active = 0
        peak = [0]

        async def fake_chat_json(*a, **kw):
            nonlocal active
            active += 1
            peak[0] = max(peak[0], active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"domain": "其他", "entities": [], "keywords": [],
                    "applicable_sections": []}

        llm = AsyncMock()
        llm.chat_json = AsyncMock(side_effect=fake_chat_json)
        tagger = tagger_factory(llm)

        mats = [_make_material(mid=i) for i in range(1, 21)]
        await tagger.tag_batch(mats, auto_retry_failed=False)

        assert peak[0] <= mt_mod._MAX_TAG_CONCURRENCY

    @pytest.mark.asyncio
    async def test_auto_retry_failed_called_at_end(self, tagger_factory):
        llm = AsyncMock()
        llm.chat_json = AsyncMock(return_value={
            "domain": "其他", "entities": [], "keywords": [],
            "applicable_sections": [],
        })
        tagger = tagger_factory(llm)

        called = {"n": 0}

        async def fake_retry():
            called["n"] += 1
            return {"retried": 0, "recovered": 0}

        tagger.retry_failed = fake_retry

        mats = [_make_material(mid=i) for i in range(1, 4)]
        await tagger.tag_batch(mats, auto_retry_failed=True)

        assert called["n"] == 1

    @pytest.mark.asyncio
    async def test_batch_tolerates_single_tag_exception(self, tagger_factory):
        """tag() 极少数未捕获异常情景下, tag_batch 应保护其他素材继续完成。"""
        llm = AsyncMock()
        llm.chat_json = AsyncMock(return_value={
            "domain": "其他", "entities": [], "keywords": [],
            "applicable_sections": [],
        })
        tagger = tagger_factory(llm)

        call_idx = {"n": 0}
        real_tag = tagger.tag

        async def flaky_tag(mat, outline_tree: str = "", *, _is_retry: bool = False):
            call_idx["n"] += 1
            if call_idx["n"] == 2:
                raise RuntimeError("simulated unhandled fault")
            return await real_tag(mat, outline_tree, _is_retry=_is_retry)

        tagger.tag = flaky_tag  # type: ignore[assignment]

        mats = [_make_material(mid=i) for i in range(1, 4)]
        results = await tagger.tag_batch(mats, auto_retry_failed=False)

        assert len(results) == 3
        assert sum(1 for r in results if r is None) == 1
        assert sum(1 for r in results if isinstance(r, dict)) == 2

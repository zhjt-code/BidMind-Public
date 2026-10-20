"""Phase4 打标接线：过滤后素材走 tag_batch，且 auto_retry_failed=True。

同形态双夹具：业绩综述 + 人员证书 两份已索引素材；未索引项不得进入打标批。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.writing.material_batch.phase4_postprocess import run_postprocess


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeHeartbeat:
    PHASE_IMAGE_CLASSIFY = "image_classify"
    PHASE_LABEL_RESOLVE = "label_resolve"
    PHASE_TAGGING = "tagging"

    def emit(self, *_args, **_kwargs) -> None:
        return None


@pytest.mark.asyncio
async def test_run_postprocess_calls_tag_batch_on_indexed_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.arq_runtime.raise_if_project_cancelled",
        lambda *_args, **_kwargs: None,
    )
    tagger = SimpleNamespace(tag_batch=AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "app.core.writer.material_tagger.get_material_tagger",
        lambda **_kwargs: tagger,
    )

    outline_nodes = [
        SimpleNamespace(depth=0, sort_order=0, title="技术标"),
        SimpleNamespace(depth=1, sort_order=0, title="技术方案"),
    ]
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult(outline_nodes)),
    )
    service = SimpleNamespace(
        db=db,
        _persist_and_classify_images=AsyncMock(),
        _refine_sub_material_titles=AsyncMock(return_value=0),
        _resolve_material_labels=AsyncMock(),
    )
    materials = [
        SimpleNamespace(id=11, title="业绩综述.pdf"),
        SimpleNamespace(id=12, title="人员证书.pdf"),
        SimpleNamespace(id=13, title="索引失败.pdf"),
    ]

    await run_postprocess(
        service,
        7,
        materials,
        {},
        {},
        {11, 12},
        _FakeHeartbeat(),
    )

    tagger.tag_batch.assert_awaited_once()
    args, kwargs = tagger.tag_batch.call_args
    assert [mat.id for mat in args[0]] == [11, 12]
    assert kwargs["auto_retry_failed"] is True
    assert "- 技术标" in kwargs["outline_tree"]
    assert "技术方案" in kwargs["outline_tree"]

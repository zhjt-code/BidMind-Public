from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.writer.industry_profiler import IndustryProfiler
from app.core.writer.outline_generator import OutlineGenerator


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_industry_profiler_reports_stage_details() -> None:
    events: list[str] = []

    async def _refresh(_clause) -> None:
        _clause.id = 101

    fake_db = SimpleNamespace(
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(side_effect=_refresh),
    )
    fake_llm = SimpleNamespace(
        chat=AsyncMock(
            return_value="一、行业识别与典型特征\n适合采用正式、稳健、合规的商务写作风格。"
        )
    )
    profiler = IndustryProfiler(db=fake_db, llm_client=fake_llm)

    clause = await profiler.generate(
        project_id=7,
        document_id=11,
        clauses=[],
        progress_callback=events.append,
    )

    assert clause.id == 101
    fake_llm.chat.assert_not_called()
    assert "参考信息不足" in clause.content
    assert clause.metadata_json["profile_confidence"] == "insufficient"
    assert events == [
        "整理行业画像上下文",
        "参考信息不足，跳过行业画像模型调用",
        "保存行业画像结果",
    ]


@pytest.mark.asyncio
async def test_outline_generator_reports_profile_progress() -> None:
    events: list[tuple[int, int, str]] = []
    nodes = [
        SimpleNamespace(
            id=1,
            parent_id=None,
            title="第一章 商务部分",
            depth=0,
            sort_order=0,
            node_type="free_writing",
            volume_type="商务卷",
            chapter_profile=None,
        ),
        SimpleNamespace(
            id=2,
            parent_id=1,
            title="投标函",
            depth=1,
            sort_order=0,
            node_type="free_writing",
            volume_type="商务卷",
            chapter_profile=None,
        ),
    ]
    fake_db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult(nodes)),
        commit=AsyncMock(),
    )
    fake_llm = SimpleNamespace(
        chat_json=AsyncMock(
            return_value=[
                {
                    "node_id": 2,
                    "semantic_role": "商务承诺",
                    "expects": ["投标声明"],
                    "rejects": ["技术路线", "施工组织设计"],
                }
            ]
        )
    )
    generator = OutlineGenerator(db=fake_db, llm_client=fake_llm)

    profiled = await generator.generate_chapter_profiles(
        7,
        progress_callback=lambda current, total, message: events.append(
            (current, total, message)
        ),
    )

    assert profiled == 1
    assert fake_db.commit.await_count == 1
    assert any("等待商务卷章节画像模型返回" in message for _, _, message in events)
    assert any(current == 1 and total == 1 for current, total, _ in events)

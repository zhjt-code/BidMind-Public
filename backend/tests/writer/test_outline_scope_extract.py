from types import SimpleNamespace

import pytest

from app.core.writer.outline_scope.contracts import CATCHALL_EXTRACT_TOPIC
from app.core.writer.outline_scope.extractor import (
    extract_scope_titles,
    parse_scope_title_payload,
    render_scope_text,
)
from app.core.writer.prompts.outline_scope import get_scope_title_prompt


def test_scope_title_prompt_requires_outline_granularity() -> None:
    system, user = get_scope_title_prompt(
        "建设内容：智慧园区平台建设",
        chapter_title="施工资质/准入",
    )

    assert "一级标题" in system
    assert "不得把技术参数" in system
    assert "与当前章节无关" in system
    assert "施工资质/准入" in user
    assert "建设内容：智慧园区平台建设" in user


def test_catchall_extract_topic_asks_demand_titles_not_misc_folder() -> None:
    _system, user = get_scope_title_prompt(
        "项目内容：地层水离子特征分析测试",
        chapter_title=CATCHALL_EXTRACT_TOPIC,
    )

    assert "项目内容" in user
    assert "建设内容" in user
    assert "其他资料" not in user


def test_parse_scope_title_payload_drops_invalid_items() -> None:
    titles = parse_scope_title_payload([
        {"title": "1. 智慧园区平台建设", "source_hint": "建设内容"},
        {"title": "响应时间指标不得低于3秒"},
        {"title": "CPU参数"},
        "数据中心机房建设",
        {"title": "短"},
    ])

    assert [item.title for item in titles] == [
        "智慧园区平台建设",
        "数据中心机房建设",
    ]


def test_render_scope_text_prefixes_page_numbers() -> None:
    chunks = [
        SimpleNamespace(page_num=18, content="第三章 建设内容"),
        SimpleNamespace(page_num=19, content="智慧园区平台建设"),
    ]

    text = render_scope_text(chunks)

    assert "[p18] 第三章 建设内容" in text
    assert "[p19] 智慧园区平台建设" in text


@pytest.mark.asyncio
async def test_extract_scope_titles_uses_llm_and_sanitizes() -> None:
    class _FakeLLM:
        async def chat_json(self, **_kwargs):
            return [
                {"title": "智慧园区平台建设", "source_hint": "建设内容"},
                {"title": "接口响应时间指标"},
            ]

    chunks = [SimpleNamespace(page_num=18, content="建设内容包括智慧园区平台建设")]
    titles = await extract_scope_titles(chunks, _FakeLLM())

    assert [item.title for item in titles] == ["智慧园区平台建设"]


@pytest.mark.asyncio
async def test_extract_scope_titles_returns_empty_on_llm_error() -> None:
    class _BoomLLM:
        async def chat_json(self, **_kwargs):
            raise RuntimeError("boom")

    chunks = [SimpleNamespace(page_num=18, content="建设内容包括智慧园区平台建设")]
    assert await extract_scope_titles(chunks, _BoomLLM()) == []

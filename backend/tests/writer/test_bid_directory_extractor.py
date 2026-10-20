""": 目录规格编排调用链防御测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.writer.bid_directory_extractor import (
    extract_bid_directory_spec,
    save_directory_spec,
)
from app.models.writing_clause import WritingClauseLayer
from app.schemas.writing import BidDirectorySpec


def _chunk(page_num: int, chunk_index: int, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        page_num=page_num,
        chunk_index=chunk_index,
        content=content,
    )


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalars(self) -> "_FakeResult":
        return self

    def first(self) -> Any:
        return self._value

    def all(self) -> list[Any]:
        if isinstance(self._value, list):
            return self._value
        return []

    def scalar_one_or_none(self) -> Any:
        return self._value


@pytest.mark.asyncio
async def test_extract_bid_directory_uses_keyword_fallback_pages_without_toc() -> None:
    chunks = [
        _chunk(2, 1, "目 录\n第五章 投标文件格式................................................"),
        _chunk(37, 2, "第五章 投标文件格式"),
        _chunk(40, 3, "技术标 目录 一、投标函"),
        _chunk(51, 4, "商务标 目录 一、投标函"),
        _chunk(60, 5, "第六章 合同条款"),
        _chunk(61, 6, "尾部无关附件"),
    ]
    captured: dict[str, str] = {}

    async def fake_chat_json(**kwargs: Any) -> dict[str, Any]:
        captured["user_message"] = kwargs["user_message"]
        return {
            "has_explicit_directory": True,
            "volumes": [
                {
                    "volume_type": "technical",
                    "volume_title": "技术标",
                    "items": [{"order": 1, "title": "投标函"}],
                },
                {
                    "volume_type": "commercial",
                    "volume_title": "商务标",
                    "items": [{"order": 1, "title": "投标函"}],
                },
            ],
            "reasoning": "命中技术/商务目录",
        }

    llm = SimpleNamespace(chat_json=fake_chat_json)
    added: list[Any] = []

    async def fake_execute(*_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(None)

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=fake_execute),
        add=lambda item: added.append(item),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    with (
        patch(
            "app.core.writer.bid_directory_extractor._load_chunks",
            AsyncMock(return_value=chunks),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.load_document_toc_map",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.resolve_runtime_params",
            return_value=(0.1, 6000),
        ),
    ):
        clause = await extract_bid_directory_spec(
            db=db,
            llm_client=llm,  # type: ignore[arg-type]
            project_id=3214,
            document_id=2356,
        )

    assert "技术标 目录 一、投标函" in captured["user_message"]
    assert "商务标 目录 一、投标函" in captured["user_message"]
    assert "尾部无关附件" not in captured["user_message"]
    assert "第六章 合同条款" not in captured["user_message"]
    assert "第五章 投标文件格式................................................" not in captured["user_message"]
    assert clause.sub_type == "bid_directory_spec"
    assert clause.layer == WritingClauseLayer.structure_constraint
    assert added
    saved = BidDirectorySpec.model_validate_json(added[0].content)
    assert saved.has_explicit_directory is True
    assert len(saved.volumes) == 2


@pytest.mark.asyncio
async def test_extract_bid_directory_prefers_toc_over_keyword_fallback() -> None:
    chunks = [
        _chunk(2, 1, "目 录\n第五章 投标文件格式................................................"),
        _chunk(37, 2, "第五章 投标文件格式"),
        _chunk(40, 3, "技术标 目录 一、投标函"),
        _chunk(99, 4, "7.1商务部分格式 目录 1.投标函"),
        _chunk(118, 5, "7.2技术部分格式 目录 1.技术方案"),
    ]
    toc_map = {
        "version": 2,
        "coordinate_system": "physical_page",
        "enabled": True,
        "entries": [
            {"title": "7.1商务部分格式", "physical_page": 99},
            {"title": "7.2技术部分格式", "physical_page": 118},
        ],
    }
    captured: dict[str, str] = {}
    keyword_calls = {"count": 0}

    async def fake_chat_json(**kwargs: Any) -> dict[str, Any]:
        captured["user_message"] = kwargs["user_message"]
        return {
            "has_explicit_directory": True,
            "volumes": [
                {
                    "volume_type": "commercial",
                    "volume_title": "商务部分格式",
                    "items": [{"order": 1, "title": "投标函"}],
                },
                {
                    "volume_type": "technical",
                    "volume_title": "技术部分格式",
                    "items": [{"order": 1, "title": "技术方案"}],
                },
            ],
            "reasoning": "toc 定向",
        }

    def spy_keywords(chunks_arg: list[Any], **_kwargs: Any) -> list[Any]:
        keyword_calls["count"] += 1
        return chunks_arg

    llm = SimpleNamespace(chat_json=fake_chat_json)
    added: list[Any] = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_FakeResult(None)),
        add=lambda item: added.append(item),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    with (
        patch(
            "app.core.writer.bid_directory_extractor._load_chunks",
            AsyncMock(return_value=chunks),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.load_document_toc_map",
            AsyncMock(return_value=toc_map),
        ),
        patch(
            "app.core.writer.directory_spec_context.select_directory_spec_chunks_by_keywords",
            side_effect=spy_keywords,
        ),
        patch(
            "app.core.writer.bid_directory_extractor.resolve_runtime_params",
            return_value=(0.1, 6000),
        ),
    ):
        await extract_bid_directory_spec(
            db=db,
            llm_client=llm,  # type: ignore[arg-type]
            project_id=1,
            document_id=2,
        )

    assert keyword_calls["count"] == 0
    assert "7.1商务部分格式" in captured["user_message"]
    assert "7.2技术部分格式" in captured["user_message"]
    assert "第五章 投标文件格式" not in captured["user_message"]


@pytest.mark.asyncio
async def test_extract_bid_directory_uses_tail_chunks_when_both_selectors_empty() -> None:
    chunks = [
        _chunk(1, 1, "普通招标要求"),
        _chunk(2, 2, "合同条款"),
        _chunk(3, 3, "尾部说明A"),
        _chunk(4, 4, "尾部说明B"),
    ]
    captured: dict[str, str] = {}

    async def fake_chat_json(**kwargs: Any) -> dict[str, Any]:
        captured["user_message"] = kwargs["user_message"]
        return {
            "has_explicit_directory": False,
            "volumes": [],
            "reasoning": "无明确目录",
        }

    llm = SimpleNamespace(chat_json=fake_chat_json)
    added: list[Any] = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_FakeResult(None)),
        add=lambda item: added.append(item),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    with (
        patch(
            "app.core.writer.bid_directory_extractor._load_chunks",
            AsyncMock(return_value=chunks),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.load_document_toc_map",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.resolve_runtime_params",
            return_value=(0.1, 6000),
        ),
    ):
        await extract_bid_directory_spec(
            db=db,
            llm_client=llm,  # type: ignore[arg-type]
            project_id=1,
            document_id=2,
        )

    assert "尾部说明A" in captured["user_message"]
    assert "尾部说明B" in captured["user_message"]


_COMM_LISTING = """第七章 投标文件格式
二、商务技术文件格式
1 投标书（实质性格式）
2 授权委托书（实质性格式）
3 开标一览表（实质性格式）
4 投标分项报价表（实质性格式）
5 合同条款偏离表（实质性格式）
6 采购需求偏离表（实质性格式）
7 本国产品标准证明文件（适用于货物类）
8 中小企业证明文件
9 拟分包情况说明
10 招标文件要求提供或投标人认为应附的其他材料
11 技术方案
"""

_PARTIAL_FOUR_ITEMS = [
    {"order": 1, "title": "投标书（实质性格式）"},
    {"order": 2, "title": "授权委托书（实质性格式）"},
    {"order": 3, "title": "开标一览表（实质性格式）"},
    {"order": 4, "title": "投标分项报价表（实质性格式）"},
]

_COMPLETE_ELEVEN_ITEMS = _PARTIAL_FOUR_ITEMS + [
    {"order": 5, "title": "合同条款偏离表（实质性格式）"},
    {"order": 6, "title": "采购需求偏离表（实质性格式）"},
    {"order": 7, "title": "本国产品标准证明文件（适用于货物类）"},
    {"order": 8, "title": "中小企业证明文件"},
    {"order": 9, "title": "拟分包情况说明"},
    {"order": 10, "title": "招标文件要求提供或投标人认为应附的其他材料"},
    {"order": 11, "title": "技术方案"},
]


def _volume_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "has_explicit_directory": True,
        "volumes": [
            {
                "volume_type": "technical",
                "volume_title": "商务技术文件格式",
                "items": items,
            }
        ],
        "reasoning": "测试夹具",
    }


def _fake_db(added: list[Any]) -> SimpleNamespace:
    return SimpleNamespace(
        execute=AsyncMock(return_value=_FakeResult(None)),
        add=lambda item: added.append(item),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_extract_bid_directory_uses_condensed_view_when_over_budget() -> None:
    listing = _COMM_LISTING
    huge = ("前置说明文字。\n" * 4000) + listing
    assert len(huge) > 30000
    chunks = [_chunk(12, 1, huge)]
    captured: list[str] = []
    added: list[Any] = []

    async def fake_chat_json(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs["user_message"])
        return _volume_payload(_COMPLETE_ELEVEN_ITEMS)

    with (
        patch(
            "app.core.writer.bid_directory_extractor._load_chunks",
            AsyncMock(return_value=chunks),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.load_document_toc_map",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.resolve_runtime_params",
            return_value=(0.1, 6000),
        ),
    ):
        await extract_bid_directory_spec(
            db=_fake_db(added),
            llm_client=SimpleNamespace(chat_json=fake_chat_json),  # type: ignore[arg-type]
            project_id=3417,
            document_id=2486,
        )

    assert captured
    assert "技术方案" in captured[0]
    assert "前置说明文字" not in captured[0]
    debug = added[0].metadata_json["extraction_debug"]
    assert debug["condensed"] is True
    assert debug["selection_path"] == "keyword"


@pytest.mark.asyncio
async def test_partial_retry_keeps_condensed_view_when_source_over_budget() -> None:
    """审查项: partial重试不得绕过输入预算发送超长全文。"""

    huge = ("前置说明文字。\n" * 4000) + _COMM_LISTING
    chunks = [_chunk(12, 1, huge)]
    captured: list[str] = []
    added: list[Any] = []

    async def fake_chat_json(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs["user_message"])
        if len(captured) == 1:
            return _volume_payload(_PARTIAL_FOUR_ITEMS)
        return _volume_payload(_COMPLETE_ELEVEN_ITEMS)

    with (
        patch(
            "app.core.writer.bid_directory_extractor._load_chunks",
            AsyncMock(return_value=chunks),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.load_document_toc_map",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.resolve_runtime_params",
            return_value=(0.1, 6000),
        ),
    ):
        await extract_bid_directory_spec(
            db=_fake_db(added),
            llm_client=SimpleNamespace(chat_json=fake_chat_json),  # type: ignore[arg-type]
            project_id=3417,
            document_id=2486,
        )

    assert len(captured) == 2
    assert all("前置说明文字" not in message for message in captured)
    assert all("技术方案" in message for message in captured)


@pytest.mark.asyncio
async def test_extract_bid_directory_retries_when_coverage_partial() -> None:
    chunks = [_chunk(12, 1, _COMM_LISTING)]
    captured: list[str] = []
    added: list[Any] = []

    async def fake_chat_json(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs["user_message"])
        if len(captured) == 1:
            return _volume_payload(_PARTIAL_FOUR_ITEMS)
        return _volume_payload(_COMPLETE_ELEVEN_ITEMS)

    with (
        patch(
            "app.core.writer.bid_directory_extractor._load_chunks",
            AsyncMock(return_value=chunks),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.load_document_toc_map",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.resolve_runtime_params",
            return_value=(0.1, 6000),
        ),
    ):
        await extract_bid_directory_spec(
            db=_fake_db(added),
            llm_client=SimpleNamespace(chat_json=fake_chat_json),  # type: ignore[arg-type]
            project_id=3417,
            document_id=2486,
        )

    assert len(captured) == 2
    saved = BidDirectorySpec.model_validate_json(added[0].content)
    assert saved.has_explicit_directory is True
    assert len(saved.volumes[0].items) == 11
    debug = added[0].metadata_json["extraction_debug"]
    assert debug["retried"] is True
    assert debug["coverage"]["status"] == "complete"
    assert "extraction_debug" in added[0].metadata_json


@pytest.mark.asyncio
async def test_extract_bid_directory_keeps_partial_with_warning() -> None:
    chunks = [_chunk(12, 1, _COMM_LISTING)]
    added: list[Any] = []

    async def fake_chat_json(**kwargs: Any) -> dict[str, Any]:
        return _volume_payload(_PARTIAL_FOUR_ITEMS)

    with (
        patch(
            "app.core.writer.bid_directory_extractor._load_chunks",
            AsyncMock(return_value=chunks),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.load_document_toc_map",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.resolve_runtime_params",
            return_value=(0.1, 6000),
        ),
    ):
        await extract_bid_directory_spec(
            db=_fake_db(added),
            llm_client=SimpleNamespace(chat_json=fake_chat_json),  # type: ignore[arg-type]
            project_id=3417,
            document_id=2486,
        )

    saved = BidDirectorySpec.model_validate_json(added[0].content)
    assert saved.has_explicit_directory is True
    assert len(saved.volumes[0].items) == 4
    meta = added[0].metadata_json
    assert meta["extraction_debug"]["retried"] is True
    assert meta["extraction_debug"]["coverage"]["status"] == "partial"
    assert "技术方案" in meta["structure_consistency_warning"]
    assert "合同条款偏离表" in str(meta["extraction_debug"]["coverage"]["missing_titles"])


_BOOKMARK_ENTRIES = [
    (1, "第一章 招标公告", 3),
    (1, "第七章 投标文件格式", 70),
    (2, "一、资格证明文件格式", 71),
    (3, "1 满足《中华人民共和国政府采购法》第二十二条规定", 72),
    (4, "1-1 营业执照等证明文件", 72),
    (4, "1-2 投标人资格声明书", 73),
    (3, "2 投标保证金凭证/交款单据电子件", 87),
    (2, "二、商务技术文件格式", 88),
    (3, "1 投标书（实质性格式）", 89),
    (3, "2 技术方案", 106),
]


@pytest.mark.asyncio
async def test_extract_bid_directory_prefers_bookmarks_and_skips_llm() -> None:
    """书签命中时零LLM照搬, selection_path=bookmark。"""

    from app.core.parser.bookmark_reader import BookmarkEntry

    added: list[Any] = []
    llm_calls = {"count": 0}

    async def fake_chat_json(**_kwargs: Any) -> dict[str, Any]:
        llm_calls["count"] += 1
        return {}

    db = SimpleNamespace(
        # 第1次: file_path 查询; 第2次: structure warning 查询
        execute=AsyncMock(
            side_effect=[_FakeResult("storage/uploads/1/tender.pdf"), _FakeResult(None)]
        ),
        add=lambda item: added.append(item),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    bookmarks = [
        BookmarkEntry(level=level, title=title, page=page)
        for level, title, page in _BOOKMARK_ENTRIES
    ]

    with (
        patch(
            "app.core.writer.bid_directory_extractor._load_chunks",
            AsyncMock(return_value=[_chunk(1, 1, "任意正文")]),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.read_pdf_bookmarks",
            return_value=bookmarks,
        ),
    ):
        await extract_bid_directory_spec(
            db=db,
            llm_client=SimpleNamespace(chat_json=fake_chat_json),  # type: ignore[arg-type]
            project_id=1,
            document_id=2,
        )

    assert llm_calls["count"] == 0
    saved = BidDirectorySpec.model_validate_json(added[0].content)
    assert saved.has_explicit_directory is True
    assert [volume.volume_title for volume in saved.volumes] == [
        "资格证明文件格式", "商务技术文件格式",
    ]
    assert saved.volumes[0].items[0].children[0].display_no == "1-1"
    assert saved.volumes[1].items[-1].title == "技术方案"
    debug = added[0].metadata_json["extraction_debug"]
    assert debug["selection_path"] == "bookmark"


@pytest.mark.asyncio
async def test_extract_bid_directory_falls_back_when_no_bookmarks() -> None:
    """无书签时回退关键词文本链路。"""

    chunks = [_chunk(12, 1, _COMM_LISTING)]
    captured: list[str] = []
    added: list[Any] = []

    async def fake_chat_json(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs["user_message"])
        return _volume_payload(_COMPLETE_ELEVEN_ITEMS)

    with (
        patch(
            "app.core.writer.bid_directory_extractor._load_chunks",
            AsyncMock(return_value=chunks),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.read_pdf_bookmarks",
            return_value=[],
        ),
        patch(
            "app.core.writer.bid_directory_extractor.load_document_toc_map",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.core.writer.bid_directory_extractor.resolve_runtime_params",
            return_value=(0.1, 6000),
        ),
    ):
        await extract_bid_directory_spec(
            db=_fake_db(added),
            llm_client=SimpleNamespace(chat_json=fake_chat_json),  # type: ignore[arg-type]
            project_id=1,
            document_id=2,
        )

    assert captured, "无书签时必须回退LLM文本链路"
    debug = added[0].metadata_json["extraction_debug"]
    assert debug["selection_path"] == "keyword"


@pytest.mark.asyncio
async def test_save_directory_spec_strips_locator_item_wrappers() -> None:
    """落库前剥离 items 中的格式章包装，编号条目提升到卷根。"""
    added: list[Any] = []
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "other",
                "volume_title": "电子投标文件格式",
                "items": [
                    {
                        "order": 1,
                        "title": "第六章 投标文件格式",
                        "children": [
                            {"order": 1, "title": "投标函"},
                            {"order": 2, "title": "授权委托书"},
                        ],
                    },
                    {"order": 2, "title": "投标保证金"},
                ],
            }
        ],
        reasoning="llm 把章标题写进了 items",
    )
    await save_directory_spec(_fake_db(added), 961, 1, spec)
    saved = BidDirectorySpec.model_validate_json(added[0].content)
    assert saved.volumes[0].volume_title == "电子投标文件格式"
    assert [item.title for item in saved.volumes[0].items] == [
        "投标函",
        "授权委托书",
        "投标保证金",
    ]

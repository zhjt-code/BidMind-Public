"""审查结果页码摘录装配防御测试。

形态: 预览高亮需要页级原文, 不能用 LLM 摘要冒充。
夹具含: 商务标页摘录(本案例)、第一/第二信封(同形态第二例)、整本回退文档 id。
"""

from app.services.review_page_excerpts import attach_page_excerpts, clip_excerpt


def test_split_envelope_uses_matching_chunk_preview() -> None:
    item = {
        "source_page_numbers": [3],
        "source_page_locations": [
            {"document_id": 11, "part": "commercial", "page": 3},
        ],
    }
    attach_page_excerpts(
        item,
        retrieval_chunks=[
            {
                "document_id": 11,
                "page_num": 3,
                "role": "primary",
                "content_preview": "投标人须提供安全生产许可证原件",
            },
            {
                "document_id": 12,
                "page_num": 3,
                "role": "primary",
                "content_preview": "技术标无关段落",
            },
        ],
        fallback_document_id=11,
    )

    assert item["source_page_locations"][0]["excerpt"] == "投标人须提供安全生产许可证原件"
    assert item["source_page_excerpts"] == [
        {"document_id": 11, "page": 3, "excerpt": "投标人须提供安全生产许可证原件"},
    ]


def test_second_morphology_first_second_envelope() -> None:
    item = {
        "source_page_numbers": [9],
        "source_page_locations": [
            {"document_id": 32, "part": "second_envelope", "page": 9},
        ],
    }
    attach_page_excerpts(
        item,
        retrieval_chunks=[
            {
                "document_id": 32,
                "page_num": 9,
                "role": "deep",
                "content_preview": "第二信封不得出现报价",
            }
        ],
        fallback_document_id=31,
    )

    assert item["source_page_excerpts"] == [
        {"document_id": 32, "page": 9, "excerpt": "第二信封不得出现报价"},
    ]


def test_integral_fallback_document_without_locations() -> None:
    item = {
        "source_page_numbers": [5],
        "source_page_locations": [],
    }
    attach_page_excerpts(
        item,
        retrieval_chunks=[
            {
                "document_id": 21,
                "page_num": 5,
                "role": "primary",
                "content_preview": "整本投标文件业绩一览表",
            }
        ],
        fallback_document_id=21,
    )

    assert item["source_page_locations"] == []
    assert item["source_page_excerpts"] == [
        {"document_id": 21, "page": 5, "excerpt": "整本投标文件业绩一览表"},
    ]


def test_missing_chunk_text_yields_empty_excerpts() -> None:
    item = {
        "source_page_numbers": [2],
        "source_page_locations": [
            {"document_id": 11, "part": "commercial", "page": 2},
        ],
    }
    attach_page_excerpts(
        item,
        retrieval_chunks=[{"document_id": 11, "page_num": 2, "role": "primary"}],
        fallback_document_id=11,
    )
    assert item["source_page_excerpts"] == []
    assert "excerpt" not in item["source_page_locations"][0]


def test_clip_excerpt_drops_none_and_caps_length() -> None:
    assert clip_excerpt(None) == ""
    assert clip_excerpt("  ") == ""
    assert clip_excerpt("a" * 300) == "a" * 280


def test_chunk_preview_not_leaked_on_retrieval_chunks() -> None:
    chunks = [
        {
            "document_id": 11,
            "page_num": 3,
            "role": "primary",
            "content_preview": "投标人须提供安全生产许可证原件",
            "content": "投标人须提供安全生产许可证原件全文",
        }
    ]
    item = {
        "source_page_numbers": [3],
        "source_page_locations": [
            {"document_id": 11, "part": "commercial", "page": 3},
        ],
    }
    attach_page_excerpts(
        item,
        retrieval_chunks=chunks,
        fallback_document_id=11,
    )
    assert "content_preview" not in chunks[0]
    assert "content" not in chunks[0]
    assert item["source_page_excerpts"][0]["excerpt"] == "投标人须提供安全生产许可证原件"

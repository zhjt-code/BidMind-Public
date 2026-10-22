"""chunk_context 单元测试: 编码文本块头注入.

防御目标 (P2):
- 块头由 section_path / clause_no 构造, 置于文本最前 (CPU 路径 max_seq=512 截断时不丢上下文).
- 元数据为空时原样返回 content, 不污染纯文本.
- chunk.content 原文不被改写 (入库内容保持干净).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.chunk_context import build_embedding_text
from app.core.parser.text_chunker import TextChunk


def _make_chunk(content: str, metadata: dict | None = None) -> TextChunk:
    return TextChunk(
        chunk_index=0,
        content=content,
        char_count=len(content),
        start_char=0,
        end_char=len(content),
        metadata=dict(metadata) if metadata else {},
    )


def test_header_built_from_section_path_and_placed_at_front() -> None:
    chunk = _make_chunk(
        "投标人应保证设备无故障运行时间不少于 8000 小时。",
        {"section_path": "第三章 技术要求"},
    )

    text = build_embedding_text(chunk)

    assert text.startswith("[第三章 技术要求] ")
    assert text.endswith("投标人应保证设备无故障运行时间不少于 8000 小时。")


def test_header_combines_clause_no_when_not_redundant() -> None:
    chunk = _make_chunk(
        "投标人须提供近 3 年财务审计报告。",
        {"section_path": "第二章 资格要求", "clause_no": "2.1.3"},
    )

    text = build_embedding_text(chunk)

    assert text.startswith("[第二章 资格要求 > 2.1.3] ")


def test_clause_no_not_appended_when_already_in_section_path() -> None:
    chunk = _make_chunk(
        "投标人应具备相应资质。",
        {"section_path": "2.1.3 资质条款", "clause_no": "2.1.3"},
    )

    text = build_embedding_text(chunk)

    assert text.count("2.1.3") == 1


def test_empty_metadata_returns_content_as_is() -> None:
    chunk = _make_chunk("普通段落内容", {})

    text = build_embedding_text(chunk)

    assert text == "普通段落内容"


def test_metadata_with_none_values_returns_content_as_is() -> None:
    chunk = _make_chunk(
        "另一段普通内容",
        {"section_path": None, "clause_no": None, "chunk_type": "paragraph"},
    )

    text = build_embedding_text(chunk)

    assert text == "另一段普通内容"


def test_build_does_not_mutate_original_content() -> None:
    original = "保留原文不可改写"
    chunk = _make_chunk(original, {"section_path": "第一章"})

    _ = build_embedding_text(chunk)

    assert chunk.content == original


def test_overlong_header_is_truncated() -> None:
    long_section = "第一章 " + ("超长章节标题描述" * 20)
    chunk = _make_chunk("正文", {"section_path": long_section})

    text = build_embedding_text(chunk)

    assert text.endswith("正文")
    header_part = text.split("] ", 1)[0]
    assert len(header_part) <= 82

"""chunk_quality 单元测试: 主链路质量门控.

防御目标 (P0):
- rejected: 空串 / 纯页码 / 纯符号 / 有效字符过少 -> 丢弃 (堵 同源乱码块前的杂质入库).
- degraded: compat_ratio > 阈值 -> 保留并标记 (教训, 不丢真证据).
- clean: 正常内容通过.
- filter_chunks 不改写 content 原文.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.chunk_quality import assess_chunk, filter_chunks
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


def test_empty_content_rejected() -> None:
    assert assess_chunk("") == "rejected"
    assert assess_chunk("   \n  ") == "rejected"


def test_pure_page_number_rejected() -> None:
    assert assess_chunk("12") == "rejected"
    assert assess_chunk("- 12 -") == "rejected"
    assert assess_chunk("第 12 页") == "rejected"
    assert assess_chunk("Page 12") == "rejected"
    assert assess_chunk("12 / 200") == "rejected"


def test_pure_punctuation_rejected() -> None:
    assert assess_chunk("......") == "rejected"
    assert assess_chunk("————————") == "rejected"
    assert assess_chunk("____") == "rejected"
    assert assess_chunk("……‧‧‧") == "rejected"


def test_too_few_valid_chars_rejected() -> None:
    # 有效字符数低于默认下限 (10) 视为无意义
    assert assess_chunk("摘要") == "rejected"
    assert assess_chunk("abc 12") == "rejected"


def test_garbled_content_degraded_but_kept() -> None:
    # 用康熙部首区字符 (U+2F00 区) 模拟 PDF 字体映射异常,
    # 总长度足够通过 min_valid_chars 校验, 但 compat_ratio 应远超 2% -> degraded
    garbled = "⼀⼆⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊"
    verdict = assess_chunk(garbled)
    assert verdict == "degraded"


def test_clean_content_passes() -> None:
    assert assess_chunk("投标人应具备建筑工程施工总承包二级及以上资质。") == "clean"
    assert assess_chunk("3.2.1 项目经理须具备一级建造师资格。") == "clean"


def test_filter_chunks_drops_rejected_and_marks_degraded() -> None:
    chunks = [
        _make_chunk("12"),
        _make_chunk("......"),
        _make_chunk("投标人应保证设备无故障运行时间不少于 8000 小时。"),
        _make_chunk("⼀⼆⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊⼊"),
    ]

    kept, stats = filter_chunks(chunks)

    assert stats.total == 4
    assert stats.rejected == 2
    assert stats.degraded == 1
    assert stats.clean == 1
    assert len(kept) == 2

    degraded_chunks = [c for c in kept if c.metadata.get("quality_flag") == "degraded"]
    assert len(degraded_chunks) == 1


def test_filter_does_not_mutate_content() -> None:
    original = "投标人须提供营业执照副本扫描件。"
    chunks = [_make_chunk(original, {"section_path": "第二章"})]

    kept, _ = filter_chunks(chunks)

    assert kept[0].content == original
    assert kept[0].metadata.get("section_path") == "第二章"
    assert kept[0].metadata.get("quality_flag") is None


def test_filter_empty_input_returns_empty() -> None:
    kept, stats = filter_chunks([])
    assert kept == []
    assert stats.total == 0
    assert stats.rejected == 0


def test_clean_content_with_explicit_threshold() -> None:
    text = "本项目位于塔里木盆地南缘,主要勘探目的层为石炭系。"
    verdict = assess_chunk(text, compat_threshold=0.5)
    assert verdict == "clean"

"""分块证据补强工具。"""

from __future__ import annotations

from app.core.parser.chunk_planner import ChunkPlanner
from app.core.parser.image_extractor import build_natural_evidence_chunk
from app.core.parser.pdf_parser import ParsedPage
from app.core.parser.text_chunker import TextChunk


def plan_pages_with_image_evidence(
    planner: ChunkPlanner,
    pages: list[ParsedPage],
) -> list[TextChunk]:
    """按结构化页面分块，并为图片页追加独立自然语言证据 chunk。"""
    chunks = planner.plan_parsed_pages(pages)
    append_image_evidence_chunks(planner, pages, chunks)
    return chunks


def append_image_evidence_chunks(
    planner: ChunkPlanner,
    pages: list[ParsedPage],
    chunks: list[TextChunk],
) -> None:
    """为包含图片的页面追加与主处理管线一致的独立证据 chunk。"""
    for index, page in enumerate(pages):
        if not page.images:
            continue
        evidence_text = build_natural_evidence_chunk(
            page_num=page.page_num,
            images=page.images,
            context=_page_context(pages, index),
            # 与 chunk_stage 一致, 整页识别文本只拼一次
            full_page_text=getattr(page, "full_page_visual_content", ""),
        )
        if not evidence_text or len(evidence_text) < 20:
            continue
        chunk = planner.build_manual_chunk(
            content=evidence_text,
            page_num=page.page_num,
            chunk_type="image_evidence",
            parent_block_id=f"image-evidence:{page.page_num}",
            section_path="图片证据",
        )
        chunk.chunk_index = len(chunks)
        chunk.metadata["is_evidence"] = True
        chunks.append(chunk)


def _page_context(pages: list[ParsedPage], index: int) -> str:
    """当前页文字放首位(: 本页标题须进入图片证据上下文), 再拼邻页窗口。"""
    texts: list[str] = []
    own_text = (pages[index].text or "").strip()
    if own_text:
        texts.append(own_text)
    for offset in (-2, -1, 1, 2):
        neighbor_index = index + offset
        if 0 <= neighbor_index < len(pages):
            text = pages[neighbor_index].text or ""
            if text.strip():
                texts.append(text.strip())
    return "\n".join(texts)

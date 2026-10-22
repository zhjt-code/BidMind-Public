"""chat-PDF 救援输入装配(子PDF组装)。

职责: 非绿救援触发后按需装配 PDF 直读输入——经 db 加载原 PDF 路径, 按评分
      chunk 页码与独立定位器页集的并集(±margin 扩页)截取为仅含评分区的
      子PDF(fitz insert_pdf), 锚定门禁用截取页 PDF 文本层(独立于缺损 chunk)。
不做: 不调用 LLM、不做门禁校验、不判定是否触发救援、不渲染图片。
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from loguru import logger


@dataclass(frozen=True)
class PdfChatRescueInput:
    """chat-PDF 救援输入快照。"""

    pdf_bytes: bytes  # 仅含评分区页面的子PDF
    pdf_base64: str  # 子PDF base64(供消息内文件块方言)
    filename: str  # 上传文件名(ASCII, 带原页码范围)
    pages: tuple[int, ...]  # 截取的原文档页码(1-based, 连续)
    anchor_text: str  # 截取页 PDF 文本层(空白归一), 锚定门禁唯一依据
    truncated: bool  # 页数超上限被截断(记入诊断)


def _assemble_subpdf_sync(
    file_path: str,
    chunk_pages: set[int],
    page_margin: int,
    max_pages: int,
) -> tuple[list[int], bytes, list[str], bool] | None:
    import fitz

    from app.core.extractor.scoring.vlm.rescue_input_builder import (
        _normalize_page_texts,
        collect_rescue_pages,
    )
    from app.core.extractor.scoring.vlm.scoring_page_locator import locate_scoring_pages

    with fitz.open(file_path) as doc:
        page_texts = {
            index + 1: doc[index].get_text() or ""
            for index in range(doc.page_count)
        }
        located = locate_scoring_pages(page_texts)
        target_pages, truncated = collect_rescue_pages(
            chunk_pages,
            located,
            page_margin=page_margin,
            max_pages=max_pages,
            page_count=doc.page_count,
        )
        if not target_pages:
            return None
        sub_doc = fitz.open()
        try:
            for page_no in target_pages:
                sub_doc.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
            pdf_bytes = sub_doc.tobytes(garbage=3, deflate=True)
        finally:
            sub_doc.close()
        texts = [page_texts.get(page_no, "") for page_no in target_pages]
        anchor = _normalize_page_texts(texts)
        return target_pages, pdf_bytes, [anchor], truncated


async def build_pdf_chat_rescue_input(
    *,
    db: Any,
    document_id: int,
    scoring_chunks: list[Any],
    page_margin: int,
    max_pages: int,
) -> Optional[PdfChatRescueInput]:
    """装配 chat-PDF 救援输入; 任一前提缺失返回 None(fail-open, 交由 VLM 续接)。"""
    if db is None:
        return None

    from sqlalchemy import select

    from app.models.document import Document

    try:
        doc = (
            await db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 - 查询失败不阻断主流程
        logger.warning("[评分PDF直读救援] Document 查询失败 doc={}: {}", document_id, exc)
        return None
    file_path = getattr(doc, "file_path", None) if doc else None
    if not file_path or not Path(file_path).exists():
        logger.warning(
            "[评分PDF直读救援] 原PDF不可用, 跳过 doc={} path={}", document_id, file_path
        )
        return None

    from app.core.extractor.scoring.vlm.rescue_input_builder import (
        _collect_scoring_pages,
    )

    chunk_pages = _collect_scoring_pages(list(scoring_chunks or []))
    try:
        assembled = await asyncio.to_thread(
            _assemble_subpdf_sync,
            str(file_path),
            chunk_pages,
            page_margin,
            max_pages,
        )
    except Exception as exc:  # noqa: BLE001 - 组装失败不阻断主流程
        logger.warning("[评分PDF直读救援] 子PDF组装失败 doc={}: {}", document_id, exc)
        return None
    if assembled is None:
        return None
    target_pages, pdf_bytes, anchor_parts, truncated = assembled
    if not pdf_bytes:
        return None
    return PdfChatRescueInput(
        pdf_bytes=pdf_bytes,
        pdf_base64=base64.b64encode(pdf_bytes).decode("ascii"),
        filename=(
            f"doc{document_id}_scoring_p{target_pages[0]}-{target_pages[-1]}.pdf"
        ),
        pages=tuple(target_pages),
        anchor_text=anchor_parts[0],
        truncated=truncated,
    )


__all__ = ["PdfChatRescueInput", "build_pdf_chat_rescue_input"]

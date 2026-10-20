"""
职责: 把 PDF 逐页送给版面服务(发请求时选择健康实例, 失败换实例重试一次); 缺文字的标题块
      从文本层取字
不做: 不解析标题层级、不做识别(文本层取不到字的标题留空)、不在地址为空时发请求、
      失败页不抛给检查器
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from loguru import logger

from app.core.document_qa.structure_headings import _TITLE_LABELS

_DPI = 120
_MAX_ATTEMPTS = 2
# 在飞页数上限 = 实例数 x 该倍数; 渲染好的整页图只在这个窗口内常驻内存
_INFLIGHT_PER_INSTANCE = 2
_TEXTBOX_PAD_PT = 2.0


def structure_urls() -> list[str]:
    from app.core.config import settings

    if not bool(getattr(settings, "DOCUMENT_QA_STRUCTURE_ENABLED", True)):
        return []
    raw = str(getattr(settings, "DOCUMENT_QA_STRUCTURE_URLS", "") or "")
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def _open_pdf(pdf_path: str) -> Any:
    import fitz

    return fitz.open(pdf_path)


def _render_page(doc: Any, page_num: int) -> str:
    from app.core.parser.page_renderer import render_page_to_base64

    return render_page_to_base64(doc[page_num - 1], dpi=_DPI)


def _post_page(url: str, image_b64: str, timeout: float) -> list[dict[str, Any]]:
    import httpx

    response = httpx.post(
        f"{url}/structure",
        json={"image_b64": image_b64},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    blocks = payload.get("blocks") if isinstance(payload, dict) else None
    return list(blocks) if isinstance(blocks, list) else []


def _fetch_page_with_failover(
    page_num: int,
    image_b64: str,
    timeout: float,
    pool: Any,
) -> list[dict[str, Any]]:
    """最多尝试两个不同实例。两次都失败时抛出最后一次异常。"""
    last_exc: Optional[BaseException] = None
    tried: set[str] = set()
    previous: Optional[str] = None
    for _ in range(_MAX_ATTEMPTS):
        url = pool.acquire_instance(exclude=tried)
        if url is None:
            break
        if previous is not None:
            logger.warning(
                f"[文档质检] 版面页改派: page={page_num}, from={previous}, to={url}, reason={last_exc}"
            )
        try:
            blocks = _post_page(url, image_b64, timeout)
        except Exception as exc:  # noqa: BLE001
            pool.report_result(url, ok=False)
            tried.add(url)
            previous = url
            last_exc = exc
            continue
        pool.report_result(url, ok=True)
        return blocks
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("版面实例均不可用")


def _needs_title_text(block: dict[str, Any]) -> bool:
    return str(block.get("label") or "") in _TITLE_LABELS and not str(block.get("text") or "").strip()


def _analyze_page(
    page_num: int,
    image_b64: str,
    timeout: float,
    pool: Any,
) -> list[dict[str, Any]]:
    return _fetch_page_with_failover(page_num, image_b64, timeout, pool)


def _fill_titles_from_text_layer(doc: Any, pages: dict[int, list[dict[str, Any]]]) -> int:
    """缺文字的标题块取框内文本层文字; 取不到(扫描件/图片)的留空, 标题树会跳过。返回取到的个数。"""
    import fitz

    filled = 0
    for page_num, blocks in pages.items():
        targets = [block for block in blocks if _needs_title_text(block)]
        if not targets:
            continue
        page = doc[page_num - 1]
        rect = page.rect
        for block in targets:
            x0, y0, x1, y1 = block["bbox"]
            clip = fitz.Rect(
                rect.x0 + x0 * rect.width - _TEXTBOX_PAD_PT,
                rect.y0 + y0 * rect.height - _TEXTBOX_PAD_PT,
                rect.x0 + x1 * rect.width + _TEXTBOX_PAD_PT,
                rect.y0 + y1 * rect.height + _TEXTBOX_PAD_PT,
            )
            text = (page.get_textbox(clip) or "").strip()
            if text:
                block["text"] = text
                filled += 1
    return filled


def _analyze_all_pages(
    doc: Any,
    urls: list[str],
    timeout: float,
    pool: Any,
) -> dict[int, list[dict[str, Any]]]:
    """调用线程逐页渲染, 工作线程只发请求: PyMuPDF 不跨线程使用。"""
    page_count = int(doc.page_count)
    pages: dict[int, list[dict[str, Any]]] = {}
    workers = max(min(len(urls), page_count), 1)
    window = threading.BoundedSemaphore(workers * _INFLIGHT_PER_INSTANCE)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for page_num in range(1, page_count + 1):
            window.acquire()
            try:
                image_b64 = _render_page(doc, page_num)
            except Exception as exc:  # noqa: BLE001
                window.release()
                logger.warning(f"[文档质检] 版面页渲染失败, 该页退回标题检测: page={page_num}, {exc}")
                continue
            future = executor.submit(_analyze_page, page_num, image_b64, timeout, pool)
            future.add_done_callback(lambda _done: window.release())
            futures[future] = page_num
        for future in as_completed(futures):
            page_num = futures[future]
            try:
                pages[page_num] = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[文档质检] 版面页失败, 该页退回标题检测: page={page_num}, {exc}")
    return pages


def fetch_structure_pages(pdf_path: str) -> Optional[dict[int, list[dict[str, Any]]]]:
    """成功页的版面块。地址为空返回 None; 全部失败也返回 None。"""
    urls = structure_urls()
    if not urls:
        return None
    from app.core.config import settings
    from app.core.document_qa.structure_pool import get_structure_pool

    timeout = float(getattr(settings, "DOCUMENT_QA_STRUCTURE_TIMEOUT", 180))
    started = time.monotonic()
    try:
        doc = _open_pdf(pdf_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[文档质检] 版面渲染失败, 退回标题检测: {exc}")
        return None
    with doc:
        page_count = int(doc.page_count)
        pages = _analyze_all_pages(doc, urls, timeout, get_structure_pool())
        from_text_layer = _fill_titles_from_text_layer(doc, pages) if pages else 0

    titles = sum(
        1 for blocks in pages.values() for block in blocks
        if str(block.get("label") or "") in _TITLE_LABELS
    )
    untitled = sum(1 for blocks in pages.values() for block in blocks if _needs_title_text(block))
    logger.info(
        f"[文档质检] 版面分析完成: pages_ok={len(pages)}/{page_count}, titles={titles}, "
        f"text_layer={from_text_layer}, titles_without_text={untitled}, "
        f"elapsed_s={time.monotonic() - started:.1f}"
    )
    return pages or None

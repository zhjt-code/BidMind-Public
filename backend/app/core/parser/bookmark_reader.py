"""
职责: 读取PDF文件的书签(outline)条目列表
不做: 不定位格式章、不构建目录规格、不访问数据库
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger


@dataclass(frozen=True)
class BookmarkEntry:
    """PDF书签条目。

    Attributes:
        level: 书签层级，1基，最高层为1。
        title: 书签标题，已strip空白。
        page: 物理页码，1基。fitz返回-1等无效值时归一为1。
    """

    level: int
    title: str
    page: int


def read_pdf_bookmarks(pdf_path: str | Path) -> list[BookmarkEntry]:
    """读取PDF文件的书签列表。

    使用PyMuPDF读取PDF书签(outline/TOC)，按原始顺序返回。
    采用fail-open策略：任何异常或无书签情况均返回空列表，不向上层抛出异常。

    Args:
        pdf_path: PDF文件路径，接受字符串或Path对象。

    Returns:
        BookmarkEntry列表；路径无效、文件不存在、解析失败或无书签时返回[]。
    """
    import fitz  # 延迟导入，避免在未安装时影响模块加载

    path_str = str(pdf_path).strip() if pdf_path is not None else ""
    if not path_str:
        logger.debug("read_pdf_bookmarks: 路径为空，跳过读取")
        return []

    path = Path(path_str)
    if not path.exists():
        logger.debug("read_pdf_bookmarks: 文件不存在 path={}", path_str)
        return []

    doc = None
    try:
        doc = fitz.open(str(path))
        toc = doc.get_toc(simple=True)  # [[level, title, page], ...]
    except Exception as exc:  # noqa: BLE001
        logger.debug("read_pdf_bookmarks: fitz异常 path={} error={}", path_str, exc)
        return []
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass

    if not toc:
        logger.debug("read_pdf_bookmarks: 无书签 path={}", path_str)
        return []

    entries: list[BookmarkEntry] = []
    for item in toc:
        level, title, page = item[0], item[1], item[2]
        title = (title or "").strip()
        if not title:
            continue
        # fitz对损坏目标可能返回-1或0，统一归一为1
        if page < 1:
            page = 1
        entries.append(BookmarkEntry(level=level, title=title, page=page))

    return entries

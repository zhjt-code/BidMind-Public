"""
bookmark_reader 防御测试。

用例覆盖：
1. 带书签PDF → 返回4条，level/title/page正确
2. 无书签PDF → 返回[]
3. 不存在的路径 → 返回[]，不抛异常
4. 空字符串路径 → 返回[]
5. 非PDF文件（txt改名.pdf）→ 返回[]，不抛异常
"""

from pathlib import Path

import fitz

from app.core.parser.bookmark_reader import BookmarkEntry, read_pdf_bookmarks

# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_pdf_with_toc(dest: Path) -> None:
    """创建含4条标准书签的测试PDF（6页）。"""
    doc = fitz.open()
    for _ in range(6):
        doc.new_page()
    doc.set_toc([
        [1, "第一章 招标公告", 1],
        [1, "第七章 投标文件格式", 2],
        [2, "一、资格证明文件格式", 2],
        [3, "1 满足《中华人民共和国政府采购法》第二十二条规定", 3],
    ])
    doc.save(str(dest))
    doc.close()


def _make_pdf_no_toc(dest: Path) -> None:
    """创建不含书签的测试PDF（3页）。"""
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    doc.save(str(dest))
    doc.close()


# ---------------------------------------------------------------------------
# 用例 1：带书签PDF
# ---------------------------------------------------------------------------

def test_with_toc_returns_four_entries(tmp_path: Path) -> None:
    """带书签PDF应返回4条BookmarkEntry，level/title/page与写入值一致。"""
    pdf = tmp_path / "with_toc.pdf"
    _make_pdf_with_toc(pdf)

    result = read_pdf_bookmarks(pdf)

    assert len(result) == 4

    assert result[0] == BookmarkEntry(level=1, title="第一章 招标公告", page=1)
    assert result[1] == BookmarkEntry(level=1, title="第七章 投标文件格式", page=2)
    assert result[2] == BookmarkEntry(level=2, title="一、资格证明文件格式", page=2)
    assert result[3] == BookmarkEntry(
        level=3,
        title="1 满足《中华人民共和国政府采购法》第二十二条规定",
        page=3,
    )


# ---------------------------------------------------------------------------
# 用例 2：无书签PDF
# ---------------------------------------------------------------------------

def test_no_toc_returns_empty(tmp_path: Path) -> None:
    """无书签PDF应返回空列表。"""
    pdf = tmp_path / "no_toc.pdf"
    _make_pdf_no_toc(pdf)

    result = read_pdf_bookmarks(pdf)

    assert result == []


# ---------------------------------------------------------------------------
# 用例 3：文件不存在
# ---------------------------------------------------------------------------

def test_nonexistent_path_returns_empty(tmp_path: Path) -> None:
    """不存在的路径应返回空列表，不抛出任何异常。"""
    fake = tmp_path / "ghost.pdf"

    result = read_pdf_bookmarks(fake)

    assert result == []


# ---------------------------------------------------------------------------
# 用例 4：空字符串路径
# ---------------------------------------------------------------------------

def test_empty_string_path_returns_empty() -> None:
    """空字符串路径应返回空列表，不抛出任何异常。"""
    result = read_pdf_bookmarks("")

    assert result == []


# ---------------------------------------------------------------------------
# 用例 5：非PDF文件改名为 .pdf
# ---------------------------------------------------------------------------

def test_non_pdf_file_returns_empty(tmp_path: Path) -> None:
    """内容为纯文本但扩展名为.pdf的文件应返回空列表，不抛出异常。"""
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_text("这不是PDF文件内容\nsome random text", encoding="utf-8")

    result = read_pdf_bookmarks(fake_pdf)

    assert result == []

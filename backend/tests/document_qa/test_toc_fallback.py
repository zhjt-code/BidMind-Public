"""宽松目录解析: 同行式(金标形态)与两行式(第二形态实体)/索引停止/不足回空"""

from app.core.document_qa.toc_fallback import extract_loose_toc_entries


def test_inline_toc_extracted():
    """同行"标题␣页码"形态(pdfplumber 合并行后的金标3638形态)。"""
    body = {
        3: "目 录\n1 项目理解与总体目标 6\n2 国内外技术现状与问题分析 10\n3 研究内容与技术路线 14",
        4: "10 风险识别与应对 47\n附件1 项目组主要成员 50",
    }
    entries, toc_pages = extract_loose_toc_entries(body)
    assert [(e.title, e.page_num) for e in entries] == [
        ("1 项目理解与总体目标", 6),
        ("2 国内外技术现状与问题分析", 10),
        ("3 研究内容与技术路线", 14),
        ("10 风险识别与应对", 47),
        ("附件1 项目组主要成员", 50),
    ]
    assert toc_pages == {3, 4}


def test_two_line_toc_extracted_second_shape():
    """两行式"标题行+纯页码行"是同机制第二形态实体。"""
    body = {
        3: "目  录\n1 项目理解与总体目标\n6\n2 国内外技术现状与问题分析\n10\n3 研究内容与技术路线\n14",
    }
    entries, toc_pages = extract_loose_toc_entries(body)
    assert [(e.title, e.page_num) for e in entries] == [
        ("1 项目理解与总体目标", 6),
        ("2 国内外技术现状与问题分析", 10),
        ("3 研究内容与技术路线", 14),
    ]
    assert toc_pages == {3}


def test_stops_at_figure_index_section():
    """图表索引附属目录不得混入主目录条目。"""
    body = {
        3: "目录\n1 概述 6\n2 方案 10\n3 保障 14",
        5: "附:图表索引(节选)\n图目录 15\n表目录 17",
    }
    entries, _ = extract_loose_toc_entries(body)
    titles = [e.title for e in entries]
    assert "图目录" not in titles and "表目录" not in titles
    assert len(entries) == 3


def test_insufficient_pairs_returns_empty():
    body = {1: "目录\n第一章 概述 3"}
    entries, toc_pages = extract_loose_toc_entries(body)
    assert entries == [] and toc_pages == set()


def test_no_toc_marker_returns_empty():
    body = {1: "投标函及承诺\n我单位愿按招标文件完成任务 2026"}
    entries, _ = extract_loose_toc_entries(body)
    assert entries == []

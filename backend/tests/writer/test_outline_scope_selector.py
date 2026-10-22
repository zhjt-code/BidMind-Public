from types import SimpleNamespace

from app.core.writer.outline_scope.selector import select_demand_chunks, select_scope_chunks


def _chunk(page_num: int | None, chunk_index: int, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        page_num=page_num,
        chunk_index=chunk_index,
        content=content,
    )


def _v2_toc(*entries: dict) -> dict:
    return {
        "version": 2,
        "coordinate_system": "physical_page",
        "enabled": True,
        "entries": list(entries),
    }


def test_select_scope_chunks_picks_construction_chapter_from_toc() -> None:
    chunks = [
        _chunk(10, 1, "第七章 投标文件格式\n投标函格式如下。"),
        _chunk(20, 2, "第三章 建设内容\n本项目建设智慧园区平台。"),
        _chunk(21, 3, "含门禁子系统与停车子系统。"),
        _chunk(40, 4, "第四章 评分标准\n技术方案 20 分。"),
    ]
    toc = _v2_toc(
        {"title": "第七章 投标文件格式", "physical_page": 10},
        {"title": "第三章 建设内容", "physical_page": 20},
        {"title": "第四章 评分标准", "physical_page": 40},
    )

    selected = select_scope_chunks(chunks, toc)

    pages = [chunk.page_num for chunk in selected]
    assert 20 in pages
    assert 21 in pages
    assert 10 not in pages
    assert 40 not in pages


def test_select_scope_chunks_skips_format_chapter_heading() -> None:
    chunks = [
        _chunk(12, 1, "第五章 投标文件格式\n本章给出投标文件组成与格式要求。"),
        _chunk(13, 2, "投标函、授权委托书格式见附件。"),
    ]

    selected = select_scope_chunks(chunks, None)

    assert selected == []


def test_select_scope_chunks_uses_heading_when_toc_missing() -> None:
    chunks = [
        _chunk(5, 1, "投标须知前附表"),
        _chunk(18, 2, "第三章 建设内容\n1. 智慧园区平台建设\n2. 数据中心机房建设"),
        _chunk(19, 3, "平台需覆盖门禁与停车。"),
        _chunk(30, 4, "第四章 合同条款\n付款按进度支付。"),
    ]

    selected = select_scope_chunks(chunks, None)

    pages = [chunk.page_num for chunk in selected]
    assert pages == [18, 19]


def test_select_scope_chunks_picks_project_content_heading() -> None:
    chunks = [
        _chunk(8, 1, "第二章 投标须知"),
        _chunk(24, 2, "第三章 项目内容及要求\n地层水离子特征分析测试"),
        _chunk(25, 3, "腐蚀产物演化特征分析测试。"),
        _chunk(40, 4, "第四章 合同条款\n付款按进度支付。"),
    ]

    selected = select_scope_chunks(chunks, None)

    pages = [chunk.page_num for chunk in selected]
    assert 24 in pages
    assert 25 in pages
    assert 8 not in pages
    assert 40 not in pages


def test_select_scope_chunks_uses_inline_include_window() -> None:
    chunks = [
        _chunk(8, 1, "项目概况：某市信息化提升工程。"),
        _chunk(9, 2, "建设内容包括：智慧园区平台建设、数据中心机房建设。"),
        _chunk(10, 3, "上述模块应统一运维。"),
        _chunk(22, 4, "第五章 评分标准\n实施方案 15 分。"),
    ]

    selected = select_scope_chunks(chunks, None)

    pages = [chunk.page_num for chunk in selected]
    assert pages == [9, 10]


def test_select_scope_chunks_empty_when_no_demand_or_qualification_chapter() -> None:
    chunks = [
        _chunk(1, 1, "招标公告"),
        _chunk(2, 2, "投标须知：递交截止时间为北京时间17时。"),
    ]

    assert select_scope_chunks(chunks, None) == []
    assert select_scope_chunks([], None) == []


def test_select_demand_chunks_skips_qualification_heading() -> None:
    chunks = [
        _chunk(16, 2, "第二章 投标人资格要求\n电子与智能化工程资质"),
        _chunk(24, 3, "第三章 项目内容及要求\n地层水离子特征分析测试"),
        _chunk(25, 4, "腐蚀产物演化特征分析测试。"),
        _chunk(40, 5, "第四章 合同条款\n付款按进度支付。"),
    ]

    selected = select_demand_chunks(chunks, None)

    pages = [chunk.page_num for chunk in selected]
    assert 24 in pages
    assert 25 in pages
    assert 16 not in pages
    assert 40 not in pages


def test_select_scope_chunks_picks_qualification_chapter_from_heading() -> None:
    chunks = [
        _chunk(10, 1, "第七章 投标文件格式\n投标函格式如下。"),
        _chunk(16, 2, "第二章 投标人资格要求\n1. 电子与智能化工程资质\n2. 安全生产许可证"),
        _chunk(17, 3, "项目负责人应具备相关执业资格。"),
        _chunk(40, 4, "第四章 评分标准\n技术方案 20 分。"),
    ]

    selected = select_scope_chunks(chunks, None)

    pages = [chunk.page_num for chunk in selected]
    assert 16 in pages
    assert 17 in pages
    assert 10 not in pages
    assert 40 not in pages

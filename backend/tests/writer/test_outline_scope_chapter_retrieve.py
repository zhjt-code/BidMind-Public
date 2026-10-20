from types import SimpleNamespace

from app.core.writer.outline_scope.chapter_retriever import (
    chapter_query_terms,
    select_related_chunks_for_chapter,
)


def _chunk(page_num: int | None, chunk_index: int, content: str, chunk_id: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=chunk_id or chunk_index,
        page_num=page_num,
        chunk_index=chunk_index,
        content=content,
    )


def test_chapter_query_terms_come_from_title_not_domain_table() -> None:
    terms = chapter_query_terms("施工资质/准入")

    assert "施工资质" in terms
    assert "资质" in terms
    assert "准入" in terms
    assert "资格" in terms
    assert "施工" not in terms
    assert "建设内容" not in terms


def test_select_related_chunks_matches_qualify_chapter_by_title_terms() -> None:
    chunks = [
        _chunk(10, 1, "第七章 投标文件格式\n投标函格式如下。", 1),
        _chunk(16, 2, "第二章 投标人资格要求\n1. 电子与智能化工程资质\n2. 安全生产许可证", 2),
        _chunk(17, 3, "项目负责人应具备相关执业资格。", 3),
        _chunk(20, 4, "第三章 建设内容\n本项目建设智慧园区平台。", 4),
        _chunk(30, 5, "第四章 合同条款\n付款按进度支付。", 5),
    ]

    selected = select_related_chunks_for_chapter(
        chapter_title="施工资质/准入",
        chunks=chunks,
    )

    pages = [chunk.page_num for chunk in selected]
    assert 16 in pages
    assert 17 in pages
    assert 10 not in pages
    assert 20 not in pages


def test_select_related_chunks_uses_injected_hits_and_stops_at_next_chapter() -> None:
    qual = _chunk(16, 2, "第二章 投标人资格要求\n具备电子与智能化工程资质。", 2)
    more = _chunk(17, 3, "项目负责人应具备相关执业资格。", 3)
    build = _chunk(20, 4, "第三章 建设内容\n智慧园区平台建设。", 4)
    chunks = [
        _chunk(10, 1, "第七章 投标文件格式", 1),
        qual,
        more,
        build,
    ]

    selected = select_related_chunks_for_chapter(
        chapter_title="施工资质/准入",
        chunks=chunks,
        retrieved=[qual],
    )

    pages = [chunk.page_num for chunk in selected]
    assert pages == [16, 17]


def test_select_related_chunks_ignores_semantic_hits_without_heading_match() -> None:
    work = _chunk(24, 4, "第三章 项目内容及要求\n地层水离子特征分析测试", 4)
    qual = _chunk(8, 2, "投标人资格条件：科研服务专业准入证", 2)
    chunks = [
        work,
        qual,
        _chunk(9, 3, "资格后审。", 3),
    ]

    selected = select_related_chunks_for_chapter(
        chapter_title="施工资质/准入",
        chunks=chunks,
        retrieved=[work],
    )

    pages = [chunk.page_num for chunk in selected]
    assert 8 in pages
    assert 9 in pages
    assert 24 not in pages


def test_select_related_chunks_does_not_take_work_list_buried_in_invite() -> None:
    invite = _chunk(
        4,
        1,
        "防腐效能耦合机制分析测试( )质量及要求\n"
        "2 HSE华北油气分公司相关规定。\n"
        "招标范围:性能测试。\n"
        "2.5项目期限:自合同签订之日起。\n"
        "2.6资金来源:成本。\n"
        "2.7.投标人资格要求投标人应具备以下资质条件:\n"
        "( )科研服务专业准入",
        1,
    )
    qual = _chunk(
        8,
        2,
        "条款名称=投标人资格条件\n"
        "具备局、分公司科研服务专业准入证",
        2,
    )
    work = _chunk(
        24,
        4,
        "第三章 项目内容及要求\n地层水离子特征分析测试\n腐蚀产物演化特征分析测试",
        4,
    )
    chunks = [
        invite,
        _chunk(7, 3, "第二章 投标须知\n地层水离子特征分析测试 | 元", 3),
        qual,
        _chunk(
            15,
            5,
            "七、业绩考察本项目不进行业绩考察。八、本项目最高限价如下: "
            "投标人资格后审。序号 项目 单位 单价(元) 1 地层水离子特征分析测试 组 910 "
            "2 地层水细菌分布特征分析测试 组 350 "
            "3 腐蚀产物演化特征分析测试 组 1110",
            5,
        ),
        work,
    ]

    selected = select_related_chunks_for_chapter(
        chapter_title="施工资质/准入",
        chunks=chunks,
    )

    pages = [chunk.page_num for chunk in selected]
    texts = "\n".join(chunk.content for chunk in selected)
    assert 8 in pages
    assert 4 not in pages
    assert 7 not in pages
    assert 15 not in pages
    assert 24 not in pages
    assert "地层水" not in texts


def test_select_related_chunks_catchall_falls_back_to_demand_window() -> None:
    chunks = [
        _chunk(10, 1, "第七章 投标文件格式\n投标函格式如下。", 1),
        _chunk(20, 2, "第三章 建设内容\n本项目建设智慧园区平台。", 2),
        _chunk(21, 3, "含门禁子系统与停车子系统。", 3),
    ]

    selected = select_related_chunks_for_chapter(
        chapter_title="其他资料（投标人需要补充的资料，格式由投标人自拟）",
        chunks=chunks,
    )

    pages = [chunk.page_num for chunk in selected]
    assert 20 in pages
    assert 21 in pages
    assert 10 not in pages


def test_catchall_demand_window_keeps_work_list_and_skips_qualification() -> None:
    chunks = [
        _chunk(8, 2, "投标人资格条件：科研服务专业准入证", 2),
        _chunk(16, 3, "第二章 投标人资格要求\n电子与智能化工程资质", 3),
        _chunk(24, 4, "第三章 项目内容及要求\n地层水离子特征分析测试", 4),
        _chunk(25, 5, "腐蚀产物演化特征分析测试", 5),
        _chunk(40, 6, "第四章 合同条款\n付款按进度支付。", 6),
    ]

    selected = select_related_chunks_for_chapter(
        chapter_title="其他资料（投标人需要补充的资料，格式由投标人自拟）",
        chunks=chunks,
    )

    pages = [chunk.page_num for chunk in selected]
    texts = "\n".join(chunk.content for chunk in selected)
    assert 24 in pages
    assert 25 in pages
    assert "地层水" in texts
    assert 8 not in pages
    assert 16 not in pages
    assert 40 not in pages

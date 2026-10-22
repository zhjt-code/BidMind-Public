from types import SimpleNamespace

import pytest

from app.core.writer.directory_spec_context import (
    is_usable_directory_spec,
    select_directory_spec_chunks,
    select_directory_spec_chunks_by_keywords,
    select_directory_spec_chunks_with_fallback,
)
from app.core.writer.format_section_markers import (
    has_explicit_format_chapter_marker,
    has_line_anchored_format_chapter_heading,
    is_format_section_title,
)
from app.schemas.writing import BidDirectorySpec


def _chunk(page_num: int | None, chunk_index: int, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        page_num=page_num,
        chunk_index=chunk_index,
        content=content,
    )


@pytest.fixture
def bug838_mixed_toc_chunks() -> list[SimpleNamespace]:
    """总目录先出现、中段噪声、后段真实技术/商务格式章。"""

    chunks = [
        _chunk(
            2,
            1,
            "目 录\n第五章 投标文件格式................................................",
        ),
    ]
    index = 2
    for page in range(5, 36):
        chunks.append(
            _chunk(
                page,
                index,
                "投标须知 投标文件由商务标和技术标组成，按评分要求提供材料。",
            )
        )
        index += 1
    chunks.extend(
        [
            _chunk(37, index, "第五章 投标文件格式\n本章给出投标文件组成与格式要求。"),
            _chunk(
                40,
                index + 1,
                "技术标\n目 录\n一、投标函\n二、科研服务专业准入证\n三、实施方案",
            ),
            _chunk(
                51,
                index + 2,
                "商务标\n目 录\n一、投标函\n二、开标一览表\n三、开户许可证",
            ),
        ]
    )
    return chunks


def test_select_directory_spec_chunks_prefers_toc_format_pages() -> None:
    chunks = [
        _chunk(
            5,
            4,
            "总目录 7投标文件格式........92 7.1商务部分格式........95 "
            "7.2技术部分格式........114 7.3价格部分格式........119",
        ),
        _chunk(10, 20, "报名附件和授权委托书，不是投标文件格式章节正文"),
        _chunk(96, 273, "7 投标文件格式 投标文件包括但不限于本章所述内容"),
        _chunk(99, 276, "7.1商务部分格式 目录(参考格式) 1.资格性和符合性审查索引表 2.商务评分标准索引表"),
        _chunk(100, 277, "7.1.1资格性和符合性审查索引表 正文模板"),
        _chunk(118, 330, "7.2技术部分格式 目录(参考格式) 1.技术响应偏离表 2.技术方案"),
        _chunk(123, 345, "7.3价格部分格式 目录(参考格式) 1.开标一览表 2.报价汇总表 3.分项报价表"),
        _chunk(131, 360, "7.4其他 附件说明"),
    ]
    toc_map = {
        "version": 2,
        "coordinate_system": "physical_page",
        "enabled": True,
        "entries": [
            {"title": "7投标文件格式", "physical_page": 96},
            {"title": "7.1商务部分格式", "physical_page": 99},
            {"title": "7.1.1资格性和符合性审查索引表", "physical_page": 100},
            {"title": "7.2技术部分格式", "physical_page": 118},
            {"title": "7.3价格部分格式", "physical_page": 123},
            {"title": "7.4其他", "physical_page": 130},
        ],
    }

    selected = select_directory_spec_chunks(chunks, toc_map)
    selected_pages = {chunk.page_num for chunk in selected}

    assert {99, 118, 123}.issubset(selected_pages)
    assert 100 in selected_pages
    assert 5 not in selected_pages
    assert 10 not in selected_pages
    assert 105 not in selected_pages


def test_toc_anchor_collects_full_format_chapter_like_project_3419() -> None:
    """: toc 命中章节起点后必须覆盖到下一顶级章，而非只取两页。"""

    chunks = [
        _chunk(69, 227, "第六章末尾"),
        _chunk(70, 228, "第七章 投标文件格式\n投标人编制文件须知"),
        _chunk(71, 229, "一、资格证明文件格式"),
    ]
    chunks.extend(
        _chunk(page, 228 + page - 69, f"格式章正文第{page}页")
        for page in range(72, 107)
    )
    chunks.append(_chunk(107, 400, "第八章 其他事项"))
    toc_map = {
        "version": 2,
        "coordinate_system": "physical_page",
        "enabled": True,
        "entries": [
            {"title": "第六章 拟签订的合同文本", "physical_page": 59},
            {"title": "第七章 投标文件格式", "physical_page": 70},
            {"title": "第八章 其他事项", "physical_page": 107},
        ],
    }

    selected = select_directory_spec_chunks(chunks, toc_map)
    selected_pages = {chunk.page_num for chunk in selected}

    assert min(selected_pages) == 70
    assert max(selected_pages) == 106
    assert 69 not in selected_pages
    assert 107 not in selected_pages


@pytest.mark.parametrize(
    "toc_map",
    [
        None,
        {"version": 1},
        {"version": 2, "enabled": False, "coordinate_system": "physical_page"},
        {
            "version": 2,
            "enabled": True,
            "coordinate_system": "physical_page",
            "entries": [],
        },
    ],
)
def test_select_directory_spec_chunks_returns_empty_without_reliable_toc_map(
    toc_map: object,
) -> None:
    assert select_directory_spec_chunks([_chunk(2, 1, "目录")], toc_map) == []


def test_keyword_fallback_starts_at_last_explicit_format_chapter(
    bug838_mixed_toc_chunks: list[SimpleNamespace],
) -> None:
    selected = select_directory_spec_chunks_by_keywords(bug838_mixed_toc_chunks)
    selected_pages = {chunk.page_num for chunk in selected}

    assert {37, 40, 51}.issubset(selected_pages)
    assert 2 not in selected_pages
    assert all(page >= 37 for page in selected_pages)
    # 旧 30 块硬上限已删除，此处不再断言 len(selected) <= 30


def test_fallback_includes_technical_and_commercial_without_toc_map(
    bug838_mixed_toc_chunks: list[SimpleNamespace],
) -> None:
    selected = select_directory_spec_chunks_with_fallback(
        bug838_mixed_toc_chunks,
        toc_map=None,
    )
    selected_pages = {chunk.page_num for chunk in selected}

    assert 40 in selected_pages
    assert 51 in selected_pages
    assert 2 not in selected_pages


def test_shared_markers_prefer_last_explicit_format_chapter() -> None:
    toc_line = "第五章 投标文件格式................................................"
    body_line = "第五章 投标文件格式\n本章给出投标文件组成与格式要求。"

    assert has_explicit_format_chapter_marker(toc_line) is True
    assert has_explicit_format_chapter_marker(body_line) is True
    assert is_format_section_title("7.1商务部分格式") is True

    chunks = [
        _chunk(2, 1, toc_line),
        _chunk(37, 2, body_line),
        _chunk(40, 3, "技术标 目录 一、投标函"),
    ]
    selected = select_directory_spec_chunks_by_keywords(chunks)
    assert [chunk.page_num for chunk in selected] == [37, 40]


def test_keyword_fallback_does_not_return_full_document_when_format_marker_missing() -> None:
    chunks = [
        _chunk(1, 1, "普通招标要求"),
        _chunk(20, 50, "合同条款"),
    ]

    assert select_directory_spec_chunks_by_keywords(chunks) == []


def test_context_selection_keeps_reliable_toc_priority() -> None:
    chunks = [
        _chunk(2, 1, "目 录\n第五章 投标文件格式................................................"),
        _chunk(37, 2, "第五章 投标文件格式"),
        _chunk(40, 3, "技术标 目录 一、投标函"),
        _chunk(99, 4, "7.1商务部分格式 目录(参考格式) 1.投标函"),
        _chunk(118, 5, "7.2技术部分格式 目录(参考格式) 1.技术方案"),
    ]
    toc_map = {
        "version": 2,
        "coordinate_system": "physical_page",
        "enabled": True,
        "entries": [
            {"title": "7.1商务部分格式", "physical_page": 99},
            {"title": "7.2技术部分格式", "physical_page": 118},
        ],
    }

    selected = select_directory_spec_chunks_with_fallback(chunks, toc_map)
    selected_pages = {chunk.page_num for chunk in selected}

    assert selected_pages == {99, 118}
    assert 2 not in selected_pages
    assert 37 not in selected_pages
    assert 40 not in selected_pages


def test_is_usable_directory_spec_rejects_explicit_volumes_without_items() -> None:
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {"volume_type": "commercial", "volume_title": "商务部分格式", "items": []},
            {"volume_type": "technical", "volume_title": "技术部分格式", "items": []},
            {"volume_type": "pricing", "volume_title": "价格部分格式", "items": []},
        ],
    )

    assert is_usable_directory_spec(spec) is False


def test_is_usable_directory_spec_accepts_all_volumes_with_items() -> None:
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "commercial",
                "volume_title": "商务部分格式",
                "items": [{"order": 1, "title": "投标函"}],
            },
            {
                "volume_type": "technical",
                "volume_title": "技术部分格式",
                "items": [{"order": 1, "title": "技术响应偏离表"}],
            },
        ],
    )

    assert is_usable_directory_spec(spec) is True


def test_is_usable_directory_spec_rejects_partial_empty_volumes() -> None:
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "commercial",
                "volume_title": "商务部分格式",
                "items": [{"order": 1, "title": "投标函"}],
            },
            {"volume_type": "technical", "volume_title": "技术部分格式", "items": []},
        ],
    )

    assert is_usable_directory_spec(spec) is False


# ---------------------------------------------------------------------------
# 新增防御测试
# ---------------------------------------------------------------------------


def test_line_anchored_format_chapter_rejects_cross_reference() -> None:
    """交叉引用不在行首，新函数不命中；旧函数仍可命中。"""

    cross_ref = "投标人按照第七章 投标文件格式编制投标文件。"
    body_title = "第七章 投标文件格式"

    assert has_line_anchored_format_chapter_heading(cross_ref) is False
    assert has_line_anchored_format_chapter_heading(body_title) is True
    # 旧函数对交叉引用仍返回 True（语义不变）
    assert has_explicit_format_chapter_marker(cross_ref) is True


def test_line_anchored_format_chapter_rejects_toc_dotted_line() -> None:
    """目录页点线条目：新函数返回 False，旧函数返回 True。"""

    toc_line = "第五章 投标文件格式................................................"
    body_line = "第五章 投标文件格式"

    assert has_line_anchored_format_chapter_heading(toc_line) is False
    assert has_line_anchored_format_chapter_heading(body_line) is True
    # 旧函数不排除目录点线行
    assert has_explicit_format_chapter_marker(toc_line) is True


def test_keyword_selection_anchors_at_real_title_not_toc_dotted_line() -> None:
    """同时存在目录点线条目与后文真实标题时，窗口从真实标题开始。"""

    chunks = [
        _chunk(2, 1, "第五章 投标文件格式................................................"),
        _chunk(37, 2, "第五章 投标文件格式\n本章给出投标文件组成与格式要求。"),
        _chunk(40, 3, "技术标格式"),
    ]
    selected = select_directory_spec_chunks_by_keywords(chunks)
    selected_pages = {chunk.page_num for chunk in selected}

    assert 2 not in selected_pages
    assert {37, 40}.issubset(selected_pages)


def test_out_of_order_chunks_are_sorted_and_format_window_correct() -> None:
    """模拟乱序输入：page=8 表格 chunk 的 chunk_index 最大被置于列表末尾。

    - 格式章从 page 12 开始
    - page 48 含非顶级章节内容，应被收入
    - page 8 的后置 chunk 重排后在格式章窗口之前，不应进入选段
    - 选段不应因旧 30 块上限截断
    """

    chunks: list[SimpleNamespace] = []
    # page 12: 格式章正文标题
    chunks.append(_chunk(12, 1, "第七章 投标文件格式\n本章规定格式要求。"))
    # page 13-47: 格式章正文内容
    for page in range(13, 48):
        chunks.append(_chunk(page, page - 11, f"格式章正文内容，第{page}页。"))
    # page 48: 不触发 _is_next_top_level_chapter 的内容
    chunks.append(_chunk(48, 37, "11 技术方案\n各参考格式如下..."))
    # page 8: 表格 chunk，chunk_index 故意设为最大，置于列表末尾
    chunks.append(_chunk(8, 9999, "第一章 招标公告"))

    selected = select_directory_spec_chunks_by_keywords(chunks)
    selected_pages = {chunk.page_num for chunk in selected if chunk.page_num is not None}

    assert 48 in selected_pages
    # page 8 的 chunk 在格式章锚点之前，不应进入窗口
    assert min(selected_pages) >= 12


def test_no_30_chunk_cutoff() -> None:
    """格式章跨 40 个页面时，选段数量必须超过旧 30 块上限。"""

    chunks: list[SimpleNamespace] = []
    # page 12: 格式章标题
    chunks.append(_chunk(12, 1, "第七章 投标文件格式\n本章规定格式要求。"))
    # page 13-51: 格式章正文，共 39 页（合计 40 个 chunk）
    for page in range(13, 52):
        chunks.append(_chunk(page, page - 11, f"格式章正文，第{page}页。"))

    selected = select_directory_spec_chunks_by_keywords(chunks)
    assert len(selected) > 30

    selected_pages = {chunk.page_num for chunk in selected}
    assert 51 in selected_pages

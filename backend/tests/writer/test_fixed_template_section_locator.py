from types import SimpleNamespace

from app.core.writer.fixed_template.section_locator import (
    locate_format_chapter_ranges,
    render_chunks_for_ranges,
)


def _chunk(page: int, text: str, idx: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(page_num=page, chunk_index=idx if idx is not None else page, content=text)


def test_locator_finds_format_range_from_mixed_page() -> None:
    chunks = [
        _chunk(38, "合同条款末尾\n附件1:《实验测试分析价格表》\n第五章 投标文件格式\n封面及目录格式"),
        _chunk(39, "宁东油田防腐药剂及防腐工具性能测试项目\n投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(40, "目 录\n一、 投标函\n二、施工资质准入\n三、投标保证金缴纳证明"),
        _chunk(41, "一、投标函\n致:招标人\n投标人: (盖章)"),
    ]

    ranges = locate_format_chapter_ranges(chunks)

    assert ranges
    assert ranges[0].page_start <= 38
    assert ranges[0].page_end >= 41


def test_locator_falls_back_to_full_document() -> None:
    chunks = [_chunk(1, "普通招标文件正文"), _chunk(2, "无格式章节")]

    ranges = locate_format_chapter_ranges(chunks)

    assert len(ranges) == 1
    assert ranges[0].page_start == 1
    assert ranges[0].page_end == 2


def test_render_chunks_for_ranges() -> None:
    chunks = [_chunk(1, "A"), _chunk(2, "B"), _chunk(3, "C")]
    ranges = locate_format_chapter_ranges(chunks)

    rendered = render_chunks_for_ranges(chunks, ranges)

    assert "--- PAGE 1" in rendered
    assert "--- PAGE 3" in rendered


def test_locator_splits_technical_and_commercial_ranges() -> None:
    """招标文件含技术标 + 商务标双卷时, 应输出两段 SectionRange。"""
    chunks = [
        _chunk(38, "第五章 投标文件格式\n封面及目录格式"),
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(40, "目 录\n一、投标函\n二、投标保证金缴纳证明"),
        _chunk(41, "一、投标函\n致:招标人\n投标人: (盖章)"),
        _chunk(50, "第六章 商务标的投标文件格式\n投 标 文 件\n商务标\n投 标 人: (盖单位章)"),
        _chunk(51, "目 录\n一、投标函\n二、开标一览表"),
        _chunk(52, "一、投标函\n致:招标人\n投标方名称: (盖章)"),
    ]

    ranges = locate_format_chapter_ranges(chunks)

    assert len(ranges) == 2
    tech, comm = ranges[0], ranges[1]
    assert tech.volume_type == "technical"
    assert comm.volume_type == "commercial"
    assert tech.page_end < comm.page_start


def test_locator_keeps_single_range_when_only_technical() -> None:
    """仅含技术标标记时, 保持单 range 行为, volume_type 由 _classify_volume 推断。"""
    chunks = [
        _chunk(38, "第五章 投标文件格式"),
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(40, "目 录\n一、投标函"),
        _chunk(41, "一、投标函\n致:招标人"),
    ]

    ranges = locate_format_chapter_ranges(chunks)

    assert len(ranges) == 1
    assert ranges[0].volume_type == "technical"


def test_locator_keeps_single_range_when_no_volume_marker() -> None:
    """既没有技术标也没有商务标标记时, 保持单 range, volume_type=None。"""
    chunks = [
        _chunk(38, "第五章 投标文件格式"),
        _chunk(39, "目 录\n一、投标函"),
        _chunk(41, "一、投标函\n致:招标人"),
    ]

    ranges = locate_format_chapter_ranges(chunks)

    assert len(ranges) == 1
    assert ranges[0].volume_type is None


def test_locator_render_marks_volume_in_page_header() -> None:
    """渲染输出每段都带 volume= 标签, 便于 LLM 区分。"""
    chunks = [
        _chunk(38, "第五章 投标文件格式"),
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(50, "投 标 文 件\n商务标"),
        _chunk(52, "致:招标人"),
    ]

    ranges = locate_format_chapter_ranges(chunks)
    rendered = render_chunks_for_ranges(chunks, ranges)

    assert "volume=technical" in rendered
    assert "volume=commercial" in rendered


def test_locator_splits_commercial_volume_with_cross_reference_to_technical() -> None:
    """: 商务标首页含"技术标"交叉引用文本时, 信号 2(模板封面 token) 仍能切分。"""
    chunks = [
        _chunk(38, "第五章 投标文件格式"),
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(40, "目 录\n一、投标函\n二、投标保证金缴纳证明"),
        _chunk(41, "一、投标函\n致:招标人\n投标人: (盖章)"),
        _chunk(
            50,
            "投 标 文 件\n商务标\n"
            "本卷投标文件按照技术标章节执行相关条款。\n"
            "投 标 人: (盖单位章)",
        ),
        _chunk(51, "目 录\n一、投标函\n二、开标一览表"),
        _chunk(52, "一、投标函\n致:招标人\n投标方名称: (盖章)"),
    ]

    ranges = locate_format_chapter_ranges(chunks)

    assert len(ranges) == 2, "信号 2 命中后应仍切分技术 / 商务两段"
    tech, comm = ranges[0], ranges[1]
    assert tech.volume_type == "technical"
    assert comm.volume_type == "commercial"
    assert comm.page_start == 50


def test_locator_strong_marker_ignores_directory_mention() -> None:
    """ 真实样本: 招标文件目录页提及"第五章 投标文件格式........"时,
 应当被识别为目录条目而忽略, 真正的章节起点必须在后续 STRONG marker 命中页。"""
    chunks = [
        _chunk(
            2,
            "目  录\n"
            "第一章 招标公告....................1\n"
            "第二章 投标人须知................11\n"
            "第三章 评标办法..................24\n"
            "第五章 投标文件格式..............37\n"
            "第六章 合同条款..................60",
        ),
        _chunk(5, "第一章 招标公告\n本项目由<ORG_ENERGY_BRANCH>负责招标。"),
        _chunk(11, "第二章 投标人须知\n技术标 商务标 详细安排见后续章节。"),
        _chunk(38, "第五章 投标文件格式\n封面及目录格式"),
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(40, "目 录\n一、投标函\n二、投标保证金缴纳证明"),
        _chunk(41, "一、投标函\n致:招标人\n投标人: (盖章)"),
        _chunk(50, "投 标 文 件\n商务标\n投 标 人: (盖单位章)"),
        _chunk(52, "一、投标函\n致:招标人\n投标方名称: (盖章)"),
    ]

    ranges = locate_format_chapter_ranges(chunks)

    assert ranges, "应当至少有一段 range"
    earliest_start = min(r.page_start for r in ranges)
    assert earliest_start >= 37, (
        f"硬 marker 取最后一个后, range start 应当落在真章节附近(>=37), "
        f"实际 start={earliest_start}"
    )
    # 同时应切出 commercial 卷
    volumes = {r.volume_type for r in ranges}
    assert "technical" in volumes
    assert "commercial" in volumes


def test_locator_falls_back_to_weak_marker_when_no_strong() -> None:
    """无硬 marker 时, 维持旧软 marker 行为, 保持向后兼容。"""
    chunks = [
        _chunk(38, "投标文件格式"),  # 仅子串包含, 非"第N章"硬形式
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(40, "目 录\n一、投标函"),
        _chunk(41, "一、投标函\n致:招标人"),
    ]

    ranges = locate_format_chapter_ranges(chunks)

    assert ranges
    assert ranges[0].page_start <= 38
    assert ranges[0].page_end >= 41


def test_locator_prefers_last_strong_marker_over_first() -> None:
    """多个硬 marker 时取最后一个作为章节起点。"""
    chunks = [
        _chunk(2, "目 录\n第五章 投标文件格式........37"),
        _chunk(38, "第五章 投标文件格式\n封面及目录格式"),
        _chunk(41, "一、投标函\n致:招标人\n投 标 人: (盖单位章)"),
    ]

    ranges = locate_format_chapter_ranges(chunks)

    assert ranges
    assert ranges[0].page_start >= 37, "应取最后一个硬 marker (page 38), 不取 page 2"

""" 工作包2B: 页级图片证据绑定防御测试

覆盖:
1. 整页识别文本在图片证据 chunk 中只拼一次(3图共享不膨胀)
2. 逐图 visual_content 保序去重(不同的各留一次, 重复的只留一次)
3. bind_full_page_visual_content 不覆盖已有逐图结果, 只回填空位
4. 当前页标题进入图片证据上下文(append_image_evidence_chunks 路径)
5. full_page_text 默认空时输出与旧实现逐字一致(旧路径兼容)
6. generate_image_annotations 页级标签 + 重复 VL 内容不重复展示
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.parser.chunk_evidence import append_image_evidence_chunks
from app.core.parser.chunk_planner import ChunkPlanner
from app.core.parser.image_extractor import (
    IMAGE_EVIDENCE_MARKER,
    ExtractedImage,
    build_natural_evidence_chunk,
    generate_image_annotations,
)
from app.core.parser.page_image_evidence import (
    bind_full_page_visual_content,
    dedupe_visual_texts,
    page_visual_texts,
)
from app.core.parser.pdf_parser import ParsedPage

FULL_PAGE_TEXT = "安全生产许可证 证书编号(晋)JZ安许证字[2023]000000 有效期至2027年8月"


def _image(index: int, visual: str = "", spatial: str = "") -> ExtractedImage:
    img = ExtractedImage(
        image_index=index,
        page_num=611,
        width=595,
        height=842,
        image_format="jpeg",
        xref=100 + index,
        file_path=f"p611_img{index}.jpeg",
    )
    img.visual_content = visual
    img.spatial_context = spatial
    return img


def test_full_page_text_appears_once_when_shared_by_three_images() -> None:
    """同页三图共享整页文本 + full_page_text 传参: 文本只出现一次。"""
    images = [_image(i, visual=FULL_PAGE_TEXT) for i in range(3)]

    evidence = build_natural_evidence_chunk(
        page_num=611,
        images=images,
        context="",
        full_page_text=FULL_PAGE_TEXT,
    )

    assert evidence.startswith(IMAGE_EVIDENCE_MARKER)
    assert evidence.count("安全生产许可证 证书编号") == 1, evidence


def test_distinct_visual_contents_kept_once_each_duplicates_collapsed() -> None:
    """逐图不同的 visual_content 各保留一次, 重复的只保留一次。"""
    images = [
        _image(0, visual="甲公司营业执照"),
        _image(1, visual="乙类特种设备使用登记证"),
        _image(2, visual="甲公司营业执照"),
    ]

    evidence = build_natural_evidence_chunk(
        page_num=611, images=images, context=""
    )

    assert evidence.count("甲公司营业执照") == 1, evidence
    assert evidence.count("乙类特种设备使用登记证") == 1, evidence
    # 保序: 甲在乙之前
    assert evidence.index("甲公司营业执照") < evidence.index("乙类特种设备使用登记证")


def test_bind_full_page_visual_content_preserves_per_image_results() -> None:
    """绑定整页文本: 页级字段落值, 已有逐图结果不覆盖, 空位回填。"""
    kept = _image(0, visual="逐图识别的营业执照内容")
    empty = _image(1)
    page = ParsedPage(page_num=611, text="", images=[kept, empty])

    bind_full_page_visual_content(page, FULL_PAGE_TEXT)

    assert page.full_page_visual_content == FULL_PAGE_TEXT
    assert kept.visual_content == "逐图识别的营业执照内容"
    assert empty.visual_content == FULL_PAGE_TEXT

    # 空内容为 no-op, 不清空已有绑定
    bind_full_page_visual_content(page, "")
    assert page.full_page_visual_content == FULL_PAGE_TEXT


def test_evidence_chunk_contains_current_page_title() -> None:
    """当前页含"3. 安全生产许可证"标题时, 证据文本必须包含该标题。"""
    img = _image(0)
    page = ParsedPage(page_num=611, text="3. 安全生产许可证", images=[img])
    bind_full_page_visual_content(page, FULL_PAGE_TEXT)

    planner = ChunkPlanner()
    chunks: list = []
    append_image_evidence_chunks(planner, [page], chunks)

    assert len(chunks) == 1
    content = chunks[0].content
    assert "3. 安全生产许可证" in content, content
    # 整页文本仍只出现一次(页级一份 + 逐图回填被去重)
    assert content.count("安全生产许可证 证书编号") == 1, content
    assert chunks[0].metadata["chunk_type"] == "image_evidence"
    assert chunks[0].metadata["is_evidence"] is True


def test_legacy_output_identical_without_full_page_text() -> None:
    """full_page_text 默认空 + 无重复输入: 输出与旧实现逐字一致。"""
    images = [
        _image(0, visual="甲公司安全生产许可证扫描件内容"),
        _image(1, spatial="乙设备照片"),
    ]

    evidence = build_natural_evidence_chunk(
        page_num=12, images=images, context="资质证明材料章节"
    )

    expected = "\n".join([
        f"{IMAGE_EVIDENCE_MARKER} 投标文件第12页提供了以下证明材料的扫描件或复印件:",
        "甲公司安全生产许可证扫描件内容",
        "一份与\"乙设备照片\"相关的证明文件扫描件",
        "本页位于投标文件\"资质证明材料章节\"章节附近。",
    ])
    assert evidence == expected


def test_legacy_fallback_paths_unchanged() -> None:
    """无视觉/空间内容时的兜底与空串返回保持旧行为。"""
    blank = _image(0)

    with_context = build_natural_evidence_chunk(
        page_num=3, images=[blank], context="相关章节说明"
    )
    assert IMAGE_EVIDENCE_MARKER in with_context
    assert "包含1张图片附件" in with_context

    no_context = build_natural_evidence_chunk(
        page_num=3, images=[_image(1)], context=""
    )
    assert no_context == ""


def test_pure_helpers_dedupe_and_page_texts() -> None:
    images = [
        _image(0, visual=FULL_PAGE_TEXT),
        _image(1, visual="独立的逐图内容"),
        _image(2, visual=FULL_PAGE_TEXT),
    ]

    assert dedupe_visual_texts(images) == [FULL_PAGE_TEXT, "独立的逐图内容"]
    assert dedupe_visual_texts(images, exclude=(FULL_PAGE_TEXT,)) == ["独立的逐图内容"]
    assert page_visual_texts(FULL_PAGE_TEXT, images) == [
        FULL_PAGE_TEXT,
        "独立的逐图内容",
    ]
    assert page_visual_texts("", images) == [FULL_PAGE_TEXT, "独立的逐图内容"]


def test_annotations_use_full_page_label_and_skip_duplicate_vl_content() -> None:
    """页级文本首行作头部 VL识别标签; 重复 visual_content 只展示一次。"""
    images = [
        _image(0, visual=FULL_PAGE_TEXT),
        _image(1, visual=FULL_PAGE_TEXT, spatial="安全生产许可证正本"),
    ]

    annotation = generate_image_annotations(
        images, context="", full_page_text=FULL_PAGE_TEXT
    )

    header_line = annotation.strip().split("\n")[0]
    assert f"VL识别: {FULL_PAGE_TEXT}" in header_line, header_line
    # 逐图标注中相同 VL 内容只展示一次, 第二张图降级走紧邻文字
    assert annotation.count(f"VL识别: \"{FULL_PAGE_TEXT}\"") == 1, annotation
    assert "紧邻文字: \"安全生产许可证正本\"" in annotation, annotation

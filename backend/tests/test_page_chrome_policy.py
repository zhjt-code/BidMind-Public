"""Page Chrome 策略测试。

防御目标:
- 跨页重复的页眉/页脚文字和图片应作为首要过滤信号。
- 重复区域高度应由文档自身 bbox 推导, 不能依赖固定比例。
- 表格表头和正文证据图片不能被误删。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module

import pytest

pytestmark = pytest.mark.scoring


def _policy_module():
    try:
        return import_module("app.core.parser.page_chrome_policy")
    except ModuleNotFoundError:
        return None


@dataclass
class _FakeImage:
    page_num: int
    image_index: int
    width: int
    height: int
    bbox: tuple[float, float, float, float] | None
    xref: int = 0
    image_format: str = "png"
    file_path: str | None = "/tmp/fake.png"
    spatial_context: str = ""
    visual_content: str = ""


@dataclass
class _FakePage:
    page_num: int
    width: float = 600.0
    height: float = 800.0
    text_blocks: list[tuple[float, float, float, float, str]] = field(default_factory=list)
    text: str = ""


def test_policy_module_exists() -> None:
    """Page Chrome 公共策略模块必须存在。"""
    assert _policy_module() is not None


def test_repeated_header_text_learns_dynamic_body_bbox_and_cleans_text() -> None:
    mod = _policy_module()
    assert mod is not None
    pages = [
        _FakePage(
            page_num=i,
            text_blocks=[
                (40, 20, 560, 150, "EQUATOR 朱道"),
                (80, 220, 520, 420, f"第 {i} 页正文内容"),
                (250, 760, 350, 785, f"第 {i} 页"),
            ],
            text=f"EQUATOR 朱道\n第 {i} 页正文内容\n第 {i} 页",
        )
        for i in range(1, 6)
    ]

    policy = mod.learn_page_chrome(pages, min_repeat_pages=3, repeat_ratio=0.6)

    assert policy.get_body_bbox(1)[1] >= 150
    # 纯页码/短编号只做行级过滤, 不再驱动区域裁剪。
    assert policy.get_body_bbox(1)[3] == 800
    assert "EQUATOR 朱道" not in policy.clean_text(1, pages[0].text)
    cleaned_lines = policy.clean_text(1, pages[0].text).splitlines()
    assert "第 1 页" not in cleaned_lines
    assert "第 1 页正文内容" in cleaned_lines


def test_repeated_header_image_larger_than_default_band_is_filtered() -> None:
    mod = _policy_module()
    assert mod is not None
    pages = [_FakePage(page_num=i) for i in range(1, 6)]
    images_by_page = {
        i: [
            _FakeImage(
                page_num=i,
                image_index=0,
                width=600,
                height=170,
                bbox=(0, 0, 600, 170),
                xref=100 + i,
            )
        ]
        for i in range(1, 6)
    }

    policy = mod.learn_page_chrome(
        pages,
        page_images=images_by_page,
        min_repeat_pages=3,
        repeat_ratio=0.6,
    )
    kept = policy.filter_images(1, images_by_page[1])

    assert policy.get_body_bbox(1)[1] >= 170
    assert kept == []


def test_body_evidence_image_is_preserved_even_near_top() -> None:
    mod = _policy_module()
    assert mod is not None
    pages = [_FakePage(page_num=i) for i in range(1, 6)]
    logo_by_page = {
        i: [
            _FakeImage(
                page_num=i,
                image_index=0,
                width=600,
                height=120,
                bbox=(0, 0, 600, 120),
                xref=i,
            )
        ]
        for i in range(1, 6)
    }
    body_image = _FakeImage(
        page_num=1,
        image_index=1,
        width=520,
        height=620,
        bbox=(40, 130, 560, 750),
        xref=999,
        spatial_context="营业执照复印件",
    )
    logo_by_page[1].append(body_image)

    policy = mod.learn_page_chrome(pages, page_images=logo_by_page)
    kept = policy.filter_images(1, logo_by_page[1])

    assert body_image in kept
    assert all(img.image_index != 0 for img in kept)


def test_repeated_chrome_image_filters_despite_coordinate_jitter_and_context() -> None:
    """页眉页脚图片跨页坐标微抖且邻近文本含证据词时仍应过滤。"""
    mod = _policy_module()
    assert mod is not None
    pages = [_FakePage(page_num=i) for i in range(1, 6)]
    images_by_page = {
        1: [
            _FakeImage(
                page_num=1,
                image_index=0,
                width=1819,
                height=161,
                bbox=(-25.32, 0.0, 595.32, 52.8),
                spatial_context="目录 营业执照扫描件",
            )
        ],
        2: [
            _FakeImage(
                page_num=2,
                image_index=0,
                width=1819,
                height=161,
                bbox=(-19.68, 0.48, 598.32, 53.28),
                spatial_context="授权书扫描件",
            )
        ],
        3: [
            _FakeImage(
                page_num=3,
                image_index=0,
                width=1819,
                height=161,
                bbox=(-20.1, 0.2, 597.9, 53.1),
                spatial_context="证书复印件",
            )
        ],
        4: [
            _FakeImage(
                page_num=4,
                image_index=0,
                width=1819,
                height=161,
                bbox=(-19.5, 0.5, 598.0, 53.3),
                spatial_context="合同扫描件",
            )
        ],
        5: [
            _FakeImage(
                page_num=5,
                image_index=0,
                width=1819,
                height=161,
                bbox=(-19.7, 0.4, 598.2, 53.2),
                spatial_context="承诺函扫描件",
            )
        ],
    }

    policy = mod.learn_page_chrome(pages, page_images=images_by_page)

    assert policy.summary.repeated_image_signatures == 1
    assert policy.filter_images(1, images_by_page[1]) == []


def test_image_signature_normalizes_negative_zero_jitter() -> None:
    """相同页脚图的 0.0 / -0.0 坐标抖动应归一为同一签名。"""
    mod = _policy_module()
    assert mod is not None
    positive = _FakeImage(
        page_num=1,
        image_index=0,
        width=1818,
        height=247,
        bbox=(0.24, 761.16, 595.32, 842.04),
    )
    negative = _FakeImage(
        page_num=49,
        image_index=0,
        width=1818,
        height=247,
        bbox=(-19.68, 760.8, 595.56, 841.68),
    )

    assert mod.image_signature(positive, 595.35, 842.0) == mod.image_signature(
        negative, 595.3, 841.92
    )


def test_image_signature_clamps_bbox_overflow_to_page_bounds() -> None:
    """页眉图片 bbox 超出页面宽度时仍应归入同一重复签名。"""
    mod = _policy_module()
    assert mod is not None
    normal = _FakeImage(
        page_num=49,
        image_index=0,
        width=1819,
        height=161,
        bbox=(-19.68, 0.48, 598.32, 53.28),
    )
    overflow = _FakeImage(
        page_num=19,
        image_index=0,
        width=1819,
        height=161,
        bbox=(-28.56, -0.72, 840.48, 52.08),
    )

    assert mod.image_signature(normal, 595.3, 841.92) == mod.image_signature(
        overflow, 595.3, 841.92
    )


def test_repeated_table_header_in_candidate_band_is_not_treated_as_chrome() -> None:
    mod = _policy_module()
    assert mod is not None
    pages = [
        _FakePage(
            page_num=i,
            text_blocks=[
                (40, 30, 560, 55, "评分因素 评分标准 分值"),
                (40, 80, 560, 200, f"技术方案评分细则 {i}"),
            ],
            text=f"评分因素 评分标准 分值\n技术方案评分细则 {i}",
        )
        for i in range(1, 6)
    ]

    policy = mod.learn_page_chrome(pages)

    assert policy.get_body_bbox(1)[1] == 0
    assert "评分因素 评分标准 分值" in policy.clean_text(1, pages[0].text)


def test_low_information_number_near_top_does_not_crop_scoring_content() -> None:
    """: 顶部评分行号跨页重复后不得把评分尾页正文裁掉。"""
    mod = _policy_module()
    assert mod is not None
    pages = [
        _FakePage(
            page_num=i,
            text_blocks=[
                (40, 83, 560, 95, "根据供应商提供的质量保证措施和售后服务方案"),
                (240, 122, 252, 133, str(i)),
                (40, 135, 560, 150, "0～6分之间合理打分，仅提供承诺不得分"),
                (40, 220, 560, 420, f"第 {i} 页正文内容"),
            ],
            text=(
                "根据供应商提供的质量保证措施和售后服务方案\n"
                f"{i}\n"
                "0～6分之间合理打分，仅提供承诺不得分\n"
                f"第 {i} 页正文内容"
            ),
        )
        for i in range(1, 6)
    ]

    policy = mod.learn_page_chrome(pages)

    assert policy.get_body_bbox(1)[1] == 0
    cleaned = policy.clean_text(1, pages[0].text)
    assert "质量保证措施和售后服务方案" in cleaned
    assert "0～6分之间合理打分" in cleaned

"""水印预处理策略防御测试（根治层）。

页面水印字符可能散落进正文与表格单元格，污染分值提取与展示文本。

判定不变量(用户约束): 不依赖位置/角度/单页实例数——水印可能水平、可能一页多个
平铺实例、可能是图片; 核心是"承载信息恒定 + 多页重复 + 弱显示属性"。
测试数据由匿名字符和版式属性合成，不包含原始文档内容。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.watermark_policy import (  # noqa: E402
    WatermarkPolicy,
    learn_watermark_text_groups,
)

SYNTHETIC_WATERMARK = "匿名水印测试甲乙丙丁戊"


def _make_char(
    text: str,
    *,
    fontname: str = "FangSong",
    size: float = 12.0,
    color=(0, 0, 0),
    matrix=(1, 0, 0, 1),
) -> dict:
    return {
        "text": text,
        "fontname": fontname,
        "size": size,
        "non_stroking_color": color,
        "matrix": list(matrix),
        "object_type": "char",
    }


def _body_page(page_num: int, body_text: str) -> tuple[int, list[dict]]:
    return page_num, [_make_char(c) for c in body_text]


def _synthetic_char_pages() -> list[tuple[int, list[dict]]]:
    pages = []
    for page_num in (3, 15, 33, 34, 50):
        _num, body = _body_page(page_num, f"第{page_num}页匿名正文内容")
        watermark = [
            _make_char(
                char,
                fontname="STSong-Light",
                size=42.26,
                color=(0.50196, 0.50196, 0.50196),
                matrix=(0.643, 0.766, -0.766, 0.643),
            )
            for char in SYNTHETIC_WATERMARK
        ]
        pages.append((page_num, body + watermark))
    return pages


# ---------------------------------------------------------------------------
# 合成样本：灰色大字水印
# ---------------------------------------------------------------------------


def test_synthetic_watermark_group_learned_and_body_untouched():
    """五页合成样本：唯一水印组为灰色 42.3pt 文本，正文组不入选。"""
    groups = learn_watermark_text_groups(_synthetic_char_pages())

    assert len(groups) == 1
    group = groups[0]
    assert group.charset == frozenset(SYNTHETIC_WATERMARK)
    assert group.trait == "low_ink"
    assert group.pages_seen == 5
    # 正文字体组(仿宋/黑体, 纯黑)绝不入选
    assert "FangSong" not in group.fontname or group.color_key != "0.000,0.000,0.000"


def test_synthetic_policy_filters_watermark_chars_only():
    """字符过滤: 水印字符剔除, 相同文字的正文字符(黑色常规字号)保留。"""
    pages = _synthetic_char_pages()
    groups = learn_watermark_text_groups(pages)
    policy = WatermarkPolicy(
        text_groups={(g.fontname, g.size, g.color_key): g for g in groups}
    )

    page33 = dict(pages)[33]
    wm_chars = [c for c in page33 if policy.is_watermark_char(c)]
    assert len(wm_chars) == 11  # 每页恒定11字水印实例
    # 正文中的同字黑色常规字号字符必须保留。
    body_chars = [
        c for c in page33
        if c["text"] == "匿" and not policy.is_watermark_char(c)
    ]
    assert body_chars, "正文里的同字不得被误删"


# ---------------------------------------------------------------------------
# 用户约束泛化: 角度无规律 / 一页多实例 / 平铺
# ---------------------------------------------------------------------------


def test_horizontal_gray_tiled_watermark_multiple_instances_per_page():
    """水平(无旋转)灰色平铺水印, 每页实例数不同, 仍按信息恒定命中。"""
    wm = "机密文件"
    pages = []
    for page_num, repeat in ((1, 3), (2, 5), (3, 2), (4, 4)):
        _num, body = _body_page(page_num, f"第{page_num}页正文内容各不相同示例文本")
        tiles = [
            _make_char(c, fontname="SimSun", size=30.0, color=(0.8, 0.8, 0.8))
            for _ in range(repeat)
            for c in wm
        ]
        pages.append((page_num, body + tiles))

    groups = learn_watermark_text_groups(pages)
    assert len(groups) == 1
    assert groups[0].charset == frozenset(wm)
    assert groups[0].trait == "low_ink"


def test_rotated_black_watermark_hits_via_rotation_trait():
    """黑色但旋转的水印(颜色特征失效)经旋转特征命中。"""
    wm = "样本禁止外传"
    pages = []
    for page_num in (1, 2, 3, 4):
        _num, body = _body_page(page_num, f"第{page_num}页正文内容互不相同的段落")
        rotated = [
            _make_char(c, fontname="STSong", size=12.0, color=(0, 0, 0), matrix=(0.7, 0.7, -0.7, 0.7))
            for c in wm
        ]
        pages.append((page_num, body + rotated))

    groups = learn_watermark_text_groups(pages)
    assert len(groups) == 1
    assert groups[0].trait == "rotated"


# ---------------------------------------------------------------------------
# 防误删负例: 跨页续表表头 / 正文 / 页码
# ---------------------------------------------------------------------------


def test_repeated_table_header_black_regular_never_filtered():
    """跨页续表表头(黑色常规字号, 内容恒定, 每页重复)绝不入选——弱显示闸门。"""
    header = "评分标准评分内容满分分值"
    pages = []
    for page_num in (1, 2, 3, 4, 5):
        _num, body = _body_page(page_num, f"第{page_num}页各不相同的评分规则正文")
        header_chars = [_make_char(c, fontname="SimHei", size=9.0, color=(0, 0, 0)) for c in header]
        pages.append((page_num, body + header_chars))

    assert learn_watermark_text_groups(pages) == []


def test_varying_body_titles_not_filtered_even_if_large_gray():
    """内容跨页不恒定的大字标题(即便浅色)不入选——信息恒定闸门。"""
    pages = []
    for page_num, title in ((1, "第一章总则"), (2, "第二章评标办法"), (3, "第三章合同条款"), (4, "第四章附件")):
        _num, body = _body_page(page_num, f"第{page_num}页正文")
        title_chars = [_make_char(c, fontname="SimHei", size=22.0, color=(0.6, 0.6, 0.6)) for c in title]
        pages.append((page_num, body + title_chars))

    groups = learn_watermark_text_groups(pages)
    # 各页标题字符集互不相同, 覆盖率不足, 不得合成水印组
    assert groups == []


def test_few_pages_below_threshold_not_filtered():
    """复现页数低于阈值(仅2页)不入选。"""
    wm = "草稿"
    pages = []
    for page_num in (1, 2):
        _num, body = _body_page(page_num, f"第{page_num}页正文内容")
        pages.append((page_num, body + [
            _make_char(c, size=40.0, color=(0.7, 0.7, 0.7)) for c in wm
        ]))
    # 补两页无水印页, 总页数4, 阈值=max(3, ceil(4*0.6))=3 > 2
    pages.append(_body_page(3, "第3页正文"))
    pages.append(_body_page(4, "第4页正文"))

    assert learn_watermark_text_groups(pages) == []


def test_keep_object_preserves_non_char_objects():
    """page.filter 回调只剔字符: 线/框对象(表格边线)一律保留。"""
    policy = WatermarkPolicy()
    assert policy.keep_object({"object_type": "line"}) is True
    assert policy.keep_object({"object_type": "rect"}) is True
    assert policy.keep_object({"object_type": "char", "text": "任"}) is True

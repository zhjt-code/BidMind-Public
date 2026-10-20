"""
bookmark_directory_source 防御测试。

覆盖：核心样例完整形态、质量门槛（条目数/深度）、单卷形态、
多命中取最后、空列表、层级跳跃不崩。
"""

from __future__ import annotations

from typing import Any

from app.core.writer.bookmark_directory_source import build_spec_from_bookmarks
from app.schemas.writing import BidDirectorySpec


# ── 测试夹具数据 ───────────────────────────────────────────────────

def _qual_entries() -> list[tuple[int, str, int]]:
    """资格卷书签条目（从格式章标题开始）。"""
    return [
        (1, "第七章 投标文件格式", 50),
        (2, "一、资格证明文件格式", 51),
        (3, "1 满足《中华人民共和国政府采购法》第二十二条规定", 52),
        (4, "1-1 营业执照等证明文件", 53),
        (4, "1-2 投标人资格声明书", 54),
        (3, "2 落实政府采购政策需满足的资格要求（如有）", 55),
        (4, "2-1 中小企业政策证明文件", 56),
        (5, "2-1-1 中小企业证明文件", 57),
        (5, "2-1-2 拟分包情况说明及分包意向协议", 58),
        (4, "2-2 其它落实政府采购政策的资格要求（如有）", 59),
        (3, "3 本项目的特定资格要求（如有）", 60),
        (4, "3-1 联合协议（如有）", 61),
        (4, "3-2 其他特定资格要求", 62),
        (3, "4 投标保证金凭证/交款单据电子件", 63),
    ]


# 包含第一章、格式章、两个卷（资格+商务技术）、后续第八章的完整样例
CORE_ENTRIES: list[tuple[int, str, int]] = [
    (1, "第一章 招标公告", 1),
    *_qual_entries(),
    (2, "二、商务技术文件格式", 64),
    (3, "1 投标书（实质性格式）", 65),
    (3, "2 授权委托书（实质性格式）", 66),
    (3, "3 报价汇总表", 67),
    (3, "4 分项报价表", 68),
    (3, "5 项目实施方案", 69),
    (3, "6 人员配备方案", 70),
    (3, "7 质量保证措施", 71),
    (3, "8 工期计划", 72),
    (3, "9 业绩证明材料", 73),
    (3, "10 售后服务承诺", 74),
    (3, "11 技术方案", 75),
    (1, "第八章 合同条款", 76),
]


# ── 辅助函数 ──────────────────────────────────────────────────────

def _walk_items(items: list[Any], visitor: Any) -> None:
    """深度优先遍历所有条目节点。"""
    for item in items:
        visitor(item)
        _walk_items(item.children, visitor)


def _flat_display_nos(spec: BidDirectorySpec) -> list[str]:
    """展平所有非 None 的 display_no。"""
    nos: list[str] = []
    for vol in spec.volumes:
        _walk_items(vol.items, lambda it: nos.append(it.display_no) if it.display_no else None)
    return nos


def _all_source_verified(spec: BidDirectorySpec) -> bool:
    """检查所有条目 source_verified 均为 True。"""
    results: list[bool] = []
    for vol in spec.volumes:
        _walk_items(vol.items, lambda it: results.append(it.source_verified is True))
    return all(results) if results else True


# ── 测试组1：核心样例 ─────────────────────────────────────────────

def test_core_sample_returns_two_volumes() -> None:
    """核心样例应识别出资格卷和商务技术卷共2卷。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    assert len(spec.volumes) == 2


def test_core_sample_volume_types() -> None:
    """卷类型：资格卷→qualification，商务技术卷→commercial。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    types = {v.volume_type for v in spec.volumes}
    assert "qualification" in types
    assert "commercial" in types


def test_core_sample_qualification_has_four_top_items() -> None:
    """资格卷顶层条目数为4。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    qual = next(v for v in spec.volumes if v.volume_type == "qualification")
    assert len(qual.items) == 4


def test_core_sample_item1_has_two_children() -> None:
    """资格卷第1条（满足...）有且仅有2个子条目：1-1、1-2。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    qual = next(v for v in spec.volumes if v.volume_type == "qualification")
    item1 = qual.items[0]
    assert len(item1.children) == 2
    child_nos = [c.display_no for c in item1.children]
    assert "1-1" in child_nos
    assert "1-2" in child_nos


def test_core_sample_item2_children_and_grandchildren() -> None:
    """资格卷第2条有2子；2-1有2孙（2-1-1、2-1-2）；2-2无子。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    qual = next(v for v in spec.volumes if v.volume_type == "qualification")
    item2 = qual.items[1]
    assert len(item2.children) == 2
    child_21 = next(c for c in item2.children if c.display_no == "2-1")
    child_22 = next(c for c in item2.children if c.display_no == "2-2")
    assert len(child_21.children) == 2
    grandchild_nos = {gc.display_no for gc in child_21.children}
    assert "2-1-1" in grandchild_nos
    assert "2-1-2" in grandchild_nos
    assert len(child_22.children) == 0


def test_core_sample_item3_has_two_children() -> None:
    """资格卷第3条（本项目特定资格）有2个子条目：3-1、3-2。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    qual = next(v for v in spec.volumes if v.volume_type == "qualification")
    item3 = qual.items[2]
    assert len(item3.children) == 2
    child_nos = [c.display_no for c in item3.children]
    assert "3-1" in child_nos
    assert "3-2" in child_nos


def test_core_sample_commercial_has_eleven_items() -> None:
    """商务技术卷顶层条目数为11。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    comm = next(v for v in spec.volumes if v.volume_type == "commercial")
    assert len(comm.items) == 11


def test_core_sample_display_nos_correctly_extracted() -> None:
    """display_no 正确提取：存在 1-1 和 2-1-1。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    nos = _flat_display_nos(spec)
    assert "1-1" in nos
    assert "2-1-1" in nos


def test_core_sample_volume_title_stripped_of_chinese_number() -> None:
    """资格卷 volume_title 为"资格证明文件格式"（"一、"已被剥离）。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    qual = next(v for v in spec.volumes if v.volume_type == "qualification")
    assert qual.volume_title == "资格证明文件格式"


def test_core_sample_all_items_source_verified_true() -> None:
    """所有条目的 source_verified 均为 True。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    assert _all_source_verified(spec)


def test_core_sample_reasoning_contains_chapter_title() -> None:
    """reasoning 应包含格式章标题。"""
    spec = build_spec_from_bookmarks(CORE_ENTRIES)
    assert spec is not None
    assert "第七章 投标文件格式" in spec.reasoning


# ── 测试组2：质量门槛 ─────────────────────────────────────────────

def test_no_format_chapter_returns_none() -> None:
    """书签中无格式章 → None。"""
    entries = [
        (1, "第一章 招标公告", 1),
        (2, "1.1 背景", 2),
        (1, "第二章 供应商资格", 3),
        (2, "2.1 基本要求", 4),
        (2, "2.2 资质证明", 5),
    ]
    assert build_spec_from_bookmarks(entries) is None


def test_chapter_followed_by_sibling_chapter_returns_none() -> None:
    """格式章后紧接同级章节，子树为空 → None（条目数门槛）。"""
    entries = [
        (1, "第七章 投标文件格式", 50),
        (1, "第八章 合同条款", 60),
    ]
    assert build_spec_from_bookmarks(entries) is None


def test_subtree_fewer_than_five_returns_none() -> None:
    """子树条目数 < 5 → None。"""
    entries = [
        (1, "第七章 投标文件格式", 50),
        (2, "一、资格", 51),
        (2, "二、商务", 52),
        (2, "三、技术", 53),
    ]
    assert build_spec_from_bookmarks(entries) is None


def test_subtree_without_third_relative_level_returns_none() -> None:
    """子树仅有卷层（无第三相对层级）→ None（深度门槛）。"""
    entries = [
        (1, "第七章 投标文件格式", 50),
        (2, "一、资格", 51),
        (2, "二、商务", 52),
        (2, "三、技术", 53),
        (2, "四、价格", 54),
        (2, "五、综合", 55),
    ]
    assert build_spec_from_bookmarks(entries) is None


# ── 测试组3：单卷形态 ─────────────────────────────────────────────

_SINGLE_VOL_ENTRIES: list[tuple[int, str, int]] = [
    (1, "第七章 投标文件格式", 50),
    (2, "1 投标函", 51),
    (3, "1-1 投标函格式", 52),
    (2, "2 授权委托书", 53),
    (3, "2-1 委托书格式", 54),
    (2, "3 资质证明", 55),
    (2, "4 价格清单", 56),
    (2, "5 技术方案", 57),
]


def test_single_volume_when_no_separate_vol_layer() -> None:
    """格式章下直接是条目（无独立卷层，部分无子条目）→ 返回单卷。"""
    spec = build_spec_from_bookmarks(_SINGLE_VOL_ENTRIES)
    assert spec is not None
    assert len(spec.volumes) == 1


def test_single_volume_title_derived_from_chapter() -> None:
    """单卷 volume_title 保留格式章定位名，供照搬层识别并省略包装。"""
    spec = build_spec_from_bookmarks(_SINGLE_VOL_ENTRIES)
    assert spec is not None
    assert "投标文件格式" in spec.volumes[0].volume_title


# ── 测试组4：多命中取最后 ─────────────────────────────────────────

def test_last_matching_chapter_is_used() -> None:
    """两个格式章命中条目（如目录页书签在前）→ 取最后一个，子树正确。"""
    entries = [
        (2, "第七章 投标文件格式", 3),     # 目录页级书签（level 2）
        (1, "第一章 招标公告", 5),
        (1, "第七章 投标文件格式", 37),    # 正文格式章（level 1）
        (2, "一、资格证明文件格式", 38),
        (3, "1 营业执照", 39),
        (4, "1-1 营业执照扫描件", 40),
        (3, "2 资格声明书", 41),
        (4, "2-1 声明书格式", 42),
        (3, "3 保证金凭证", 43),
        (4, "3-1 保证金格式", 44),
        (2, "二、商务文件格式", 45),
        (3, "1 投标书", 46),
        (3, "2 报价单", 47),
        (3, "3 合同格式", 48),
        (3, "4 业绩证明", 49),
        (3, "5 技术方案", 50),
    ]
    spec = build_spec_from_bookmarks(entries)
    assert spec is not None
    # 以 level=1 的正文格式章为准，子树应形成两卷
    assert len(spec.volumes) == 2


# ── 测试组5：边界 ─────────────────────────────────────────────────

def test_empty_entries_returns_none() -> None:
    """空列表 → None。"""
    assert build_spec_from_bookmarks([]) is None


def test_level_jump_does_not_crash() -> None:
    """L1 直接到 L3 的层级跳跃不应崩溃，且正确通过质量门槛。"""
    entries = [
        (1, "第七章 投标文件格式", 50),
        (3, "1 直接L3条目", 51),      # 层级跳跃：L1 → L3
        (3, "2 另一L3条目", 52),
        (4, "2-1 子条目", 53),
        (3, "3 第三条", 54),
        (3, "4 第四条", 55),
        (3, "5 第五条", 56),
        (1, "第八章 合同条款", 57),
    ]
    spec = build_spec_from_bookmarks(entries)
    # 子树6条，存在 L3>=1+2=3，应通过门槛并返回单卷
    assert spec is not None
    assert len(spec.volumes) == 1

""": 招标格式章标题不得落成投标目录节点。

覆盖：locator/目录页谓词、书签过滤格式章「目录」页、照搬省略 locator 卷包装。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.writer.bookmark_directory_source import build_spec_from_bookmarks
from app.core.writer.format_section_markers import (
    is_format_chapter_locator_title,
    is_format_chapter_toc_page_title,
)
from app.core.writer.locator_volume import (
    dedup_catchall_among_siblings,
    filter_locator_toc_items,
)
from app.core.writer.outline_generator import OutlineGenerator
from app.schemas.writing import BidDirectoryItem, BidDirectorySpec, OutlineNodeGenerated

# ── 谓词 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("投标文件格式", True),
        ("第六章  投标文件格式", True),
        ("第七章投标文件格式", True),
        ("响应文件格式", True),
        ("电子投标文件格式", True),
        ("第七章电子投标文件格式", True),
        ("投标文件目录", True),
        ("六、投标文件格式", True),
        ("封面及目录格式", True),
        ("投标文件格式。", True),
        ("投标文件格式要求", False),
        ("资格证明文件格式", False),
        ("商务部分格式", False),
        ("商务技术文件格式", False),
        ("技术标", False),
        ("", False),
    ],
)
def test_locator_title_matches_format_chapter_only(
    title: str, expected: bool,
) -> None:
    assert is_format_chapter_locator_title(title) is expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("目    录", True),
        ("目录", True),
        ("一、目录", True),
        ("封面及目录", False),
        ("投标函及承诺函等", False),
        ("一、投标函及承诺函等", False),
    ],
)
def test_toc_page_title_is_format_chapter_listing_only(
    title: str, expected: bool,
) -> None:
    assert is_format_chapter_toc_page_title(title) is expected


# ── 书签：3424 形态 ───────────────────────────────────────────────

_P3424_BOOKMARKS: list[tuple[int, str, int]] = [
    (1, "第五章  供货要求", 103),
    (1, "第六章  投标文件格式", 111),
    (2, "目    录", 113),
    (2, "一、投标函及承诺函等", 114),
    (3, "（一）投标函", 115),
    (3, "（二）告知承诺函", 117),
    (3, "（三）声明函", 118),
    (2, "二、法定代表人身份证明或授权委托书", 119),
    (3, "（一）法定代表人身份证明", 120),
    (3, "（二）授权委托书", 121),
    (2, "三、投标保证金", 122),
    (2, "四、联合体协议书", 123),
    (2, "五、商务和技术偏差表", 125),
    (2, "六、分项报价表", 126),
    (2, "七、资格审查资料", 127),
    (3, "（一）投标人基本情况", 128),
    (2, "八、投标设备技术性能指标的详细描述", 146),
    (2, "九、技术支持资料", 147),
    (2, "十、技术服务和质保期服务计划", 148),
    (2, "十一、其他材料", 149),
]


def test_bookmark_spec_drops_format_chapter_toc_page() -> None:
    spec = build_spec_from_bookmarks(_P3424_BOOKMARKS)
    assert spec is not None
    titles = [item.title for item in spec.volumes[0].items]
    assert all("目录" not in t.replace(" ", "") for t in titles)
    assert titles[0] == "投标函及承诺函等"


def test_single_volume_keeps_locator_title_as_hoist_signal() -> None:
    """单卷 volume_title 保留格式章名，供照搬层识别并省略包装。"""
    spec = build_spec_from_bookmarks(_P3424_BOOKMARKS)
    assert spec is not None
    assert is_format_chapter_locator_title(spec.volumes[0].volume_title)


# ── 照搬：省略 locator 卷根 ───────────────────────────────────────

def _capture_generate(monkeypatch, spec: BidDirectorySpec) -> list[OutlineNodeGenerated]:
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    saved: list[OutlineNodeGenerated] = []

    async def _capture_save(_project_id, nodes):
        saved.extend(nodes)
        return nodes

    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_enrich_skeleton_node", AsyncMock())
    monkeypatch.setattr(generator, "_save_tree", _capture_save)
    return generator, saved


@pytest.mark.asyncio
async def test_generate_from_spec_omits_locator_volume_wrapper(monkeypatch) -> None:
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "other",
                "volume_title": "投标文件格式",
                "items": [
                    {"order": 1, "title": "目    录"},
                    {
                        "order": 2,
                        "title": "投标函及承诺函等",
                        "children": [{"order": 1, "title": "（一）投标函"}],
                    },
                    {"order": 3, "title": "投标保证金"},
                    {"order": 4, "title": "其他材料"},
                ],
            }
        ],
    )
    generator, saved = _capture_generate(monkeypatch, spec)
    await generator._generate_from_spec(3424, spec, 30000)

    root_titles = [node.title for node in saved]
    assert "投标文件格式" not in root_titles
    assert "目录" not in "".join(t.replace(" ", "") for t in root_titles)
    assert root_titles[0] == "投标函及承诺函等"
    assert saved[0].depth == 0
    assert saved[0].volume_type is None
    assert saved[0].children[0].title == "（一）投标函"
    assert saved[0].children[0].depth == 1


@pytest.mark.asyncio
async def test_generate_from_spec_keeps_real_volume_wrapper(monkeypatch) -> None:
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "technical",
                "volume_title": "技术标",
                "items": [{"order": 1, "title": "技术方案"}],
            }
        ],
    )
    generator, saved = _capture_generate(monkeypatch, spec)
    await generator._generate_from_spec(7, spec, 30000)

    assert len(saved) == 1
    assert saved[0].title == "技术标"
    assert saved[0].children[0].title == "技术方案"


def test_dedup_catchall_uses_flat_sibling_roots() -> None:
    """省略卷包装后，兜底节点按扁平根节点兄弟去重非骨架重复项。"""
    siblings = [
        OutlineNodeGenerated(
            title="投标函", depth=0, sort_order=0, node_type="fixed_template",
            is_required=1, score_weight=None, children=[], is_skeleton=True,
            volume_type=None,
        ),
        OutlineNodeGenerated(
            title="其他资料", depth=0, sort_order=1, node_type="free_writing",
            is_required=1, score_weight=None, is_skeleton=True, volume_type=None,
            children=[
                OutlineNodeGenerated(
                    title="投标函", depth=1, sort_order=0, node_type="fixed_template",
                    is_required=1, score_weight=None, children=[], is_skeleton=False,
                    volume_type=None,
                ),
                OutlineNodeGenerated(
                    title="独有附件", depth=1, sort_order=1, node_type="free_writing",
                    is_required=1, score_weight=None, children=[], is_skeleton=False,
                    volume_type=None,
                ),
            ],
        ),
    ]
    removed = dedup_catchall_among_siblings(siblings, ("其他资料",))
    assert removed == 1
    assert [c.title for c in siblings[1].children] == ["独有附件"]


def test_filter_hoists_locator_item_children() -> None:
    """章标题若被写成 items 包装节点，应删除该节点并提升其下编号条目。"""
    items = [
        BidDirectoryItem(
            order=1,
            title="第六章 投标文件格式",
            children=[
                BidDirectoryItem(order=1, title="投标函"),
                BidDirectoryItem(order=2, title="授权委托书"),
            ],
        ),
        BidDirectoryItem(order=2, title="投标保证金"),
    ]
    titles = [item.title for item in filter_locator_toc_items(items)]
    assert titles == ["投标函", "授权委托书", "投标保证金"]


def test_filter_hoists_toc_page_children() -> None:
    """印刷「目录」页若误收成带 children 的条目，应提升真实条目而不是整枝丢掉。"""
    items = [
        BidDirectoryItem(
            order=1,
            title="目    录",
            children=[
                BidDirectoryItem(order=1, title="投标函"),
                BidDirectoryItem(order=2, title="资格审查资料"),
            ],
        ),
    ]
    titles = [item.title for item in filter_locator_toc_items(items)]
    assert titles == ["投标函", "资格审查资料"]


@pytest.mark.asyncio
async def test_generate_from_spec_strips_locator_item_wrapper(monkeypatch) -> None:
    """omit 卷包装后，items 里残留的章标题不得成为 depth=0 根节点。"""
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "other",
                "volume_title": "投标文件格式",
                "items": [
                    {
                        "order": 1,
                        "title": "第六章 投标文件格式",
                        "children": [
                            {"order": 1, "title": "投标函"},
                            {"order": 2, "title": "授权委托书"},
                        ],
                    },
                    {"order": 2, "title": "投标保证金"},
                ],
            }
        ],
    )
    generator, saved = _capture_generate(monkeypatch, spec)
    await generator._generate_from_spec(961, spec, 30000)

    root_titles = [node.title for node in saved]
    assert "投标文件格式" not in root_titles
    assert "第六章 投标文件格式" not in root_titles
    assert root_titles == ["投标函", "授权委托书", "投标保证金"]
    assert all(node.depth == 0 for node in saved)


@pytest.mark.asyncio
async def test_generate_from_spec_omits_electronic_format_volume_title(
    monkeypatch,
) -> None:
    """「电子投标文件格式」是定位章名，不得落成卷包装根节点。"""
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "other",
                "volume_title": "电子投标文件格式",
                "items": [
                    {"order": 1, "title": "投标函"},
                    {"order": 2, "title": "投标保证金"},
                ],
            }
        ],
    )
    generator, saved = _capture_generate(monkeypatch, spec)
    await generator._generate_from_spec(961, spec, 30000)

    root_titles = [node.title for node in saved]
    assert "电子投标文件格式" not in root_titles
    assert root_titles == ["投标函", "投标保证金"]


def test_bookmark_duplicate_chapter_child_is_hoisted() -> None:
    """格式章下重复的「投标文件格式」子书签不得成为目录条目。"""
    entries = [
        (1, "第六章  投标文件格式", 50),
        (2, "投标文件格式", 51),
        (3, "一、投标函", 52),
        (3, "二、授权委托书", 53),
        (2, "三、投标保证金", 54),
        (2, "四、资格审查资料", 55),
        (3, "（一）基本情况", 56),
        (2, "五、技术方案", 57),
    ]
    spec = build_spec_from_bookmarks(entries)
    assert spec is not None
    titles = [item.title for item in spec.volumes[0].items]
    assert "投标文件格式" not in titles
    assert titles[0] == "投标函"

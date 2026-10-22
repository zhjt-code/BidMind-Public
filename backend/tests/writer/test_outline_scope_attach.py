from app.core.writer.outline_scope.attacher import (
    attach_scope_titles,
    attach_titles_to_parent,
    iter_enrichable_parents,
    pick_affinity_parent,
)
from app.core.writer.outline_scope.contracts import ScopeTitle
from app.schemas.writing import OutlineNodeGenerated


def _node(
    title: str,
    *,
    node_type: str = "free_writing",
    is_skeleton: bool = True,
    volume_type: str | None = "technical",
    children: list[OutlineNodeGenerated] | None = None,
    depth: int = 1,
) -> OutlineNodeGenerated:
    return OutlineNodeGenerated(
        title=title,
        depth=depth,
        sort_order=0,
        node_type=node_type,
        is_required=1,
        score_weight=None,
        children=children or [],
        is_skeleton=is_skeleton,
        volume_type=volume_type,
    )


def test_attach_scope_titles_to_leaf_technical_scheme() -> None:
    leaf = _node("技术方案")
    tree = [_node("技术标", depth=0, children=[leaf])]

    attached = attach_scope_titles(
        tree,
        [ScopeTitle("智慧园区平台建设", "建设内容")],
    )

    assert attached == 1
    assert leaf.children[0].title == "智慧园区平台建设"
    assert leaf.children[0].is_skeleton is False
    assert leaf.children[0].node_type == "free_writing"
    assert leaf.children[0].volume_type == "technical"


def test_attach_scope_titles_appends_when_skeleton_children_exist() -> None:
    leaf = _node(
        "技术方案",
        children=[_node("进度计划", is_skeleton=True, depth=2)],
    )
    tree = [_node("技术标", depth=0, children=[leaf])]

    attached = attach_scope_titles(
        tree,
        [ScopeTitle("XX系统建设", "建设内容")],
    )

    assert attached == 1
    titles = [child.title for child in leaf.children]
    assert titles == ["进度计划", "XX系统建设"]
    assert leaf.children[0].is_skeleton is True
    assert leaf.children[1].is_skeleton is False


def test_attach_scope_titles_skips_letter_and_profile() -> None:
    letter = _node("投标函", node_type="fixed_template", volume_type="qualification")
    profile = _node("公司简介", volume_type="qualification")
    tree = [
        _node(
            "资格标",
            depth=0,
            volume_type="qualification",
            children=[letter, profile],
        ),
    ]

    attached = attach_scope_titles(
        tree,
        [ScopeTitle("智慧园区平台建设", "建设内容")],
    )

    assert attached == 0
    assert letter.children == []
    assert profile.children == []


def test_iter_enrichable_parents_puts_catchall_last_and_skips_volume() -> None:
    letter = _node("投标函", node_type="fixed_template", depth=1)
    qualify = _node("施工资质/准入", depth=1)
    catchall = _node("其他资料（投标人需要补充的资料，格式由投标人自拟）", depth=1)
    volume = _node("技术标", depth=0, children=[letter, qualify, catchall])

    parents = iter_enrichable_parents([volume])

    assert [node.title for node in parents] == [
        "施工资质/准入",
        "其他资料（投标人需要补充的资料，格式由投标人自拟）",
    ]


def test_attach_titles_to_parent_keeps_construction_off_qualify_chapter() -> None:
    qualify = _node("施工资质/准入", depth=1)
    catchall = _node("其他资料（投标人需要补充的资料，格式由投标人自拟）", depth=1)
    used: set[str] = set()

    assert attach_titles_to_parent(
        qualify,
        [ScopeTitle("电子与智能化工程资质", "资格要求")],
        used_norms=used,
    ) == 1
    assert attach_titles_to_parent(
        catchall,
        [
            ScopeTitle("电子与智能化工程资质", "资格要求"),
            ScopeTitle("智慧园区平台建设", "建设内容"),
        ],
        used_norms=used,
    ) == 1

    assert [child.title for child in qualify.children] == ["电子与智能化工程资质"]
    assert [child.title for child in catchall.children] == ["智慧园区平台建设"]


def test_attach_scope_titles_merges_duplicate_and_drops_parameter_titles() -> None:
    leaf = _node(
        "技术方案",
        children=[_node("智慧园区平台建设", is_skeleton=False, depth=2)],
    )
    tree = [_node("技术标", depth=0, children=[leaf])]

    attached = attach_scope_titles(
        tree,
        [
            ScopeTitle("智慧园区平台建设", "建设内容"),
            ScopeTitle("响应时间不得低于3秒", "指标"),
            ScopeTitle("CPU参数配置", "参数"),
            ScopeTitle("数据中心机房建设", "建设内容"),
        ],
    )

    assert attached == 1
    titles = [child.title for child in leaf.children]
    assert titles == ["智慧园区平台建设", "数据中心机房建设"]


def test_pick_affinity_parent_uses_retrieved_window_then_falls_back() -> None:
    scheme = _node("技术方案")
    qualify = _node("施工资质/准入")

    picked = pick_affinity_parent(
        [qualify, scheme],
        "地层水离子特征分析测试",
        parent_windows={
            id(qualify): "投标人资格条件 电子与智能化工程资质",
            id(scheme): "第三章 建设内容\n地层水离子特征分析测试",
        },
    )
    assert picked is scheme

    assert pick_affinity_parent(
        [qualify],
        "地层水离子特征分析测试",
        parent_windows={id(qualify): "投标人资格条件 电子与智能化工程资质"},
    ) is None


def test_pick_affinity_parent_uses_chapter_title_terms() -> None:
    protect = _node("腐蚀防护方案")
    qualify = _node("施工资质/准入")

    picked = pick_affinity_parent(
        [qualify, protect],
        "腐蚀产物演化特征分析测试",
        parent_windows={
            id(qualify): "投标人资格条件",
            id(protect): "",
        },
    )
    assert picked is protect


def test_attach_scope_titles_empty_input_keeps_tree() -> None:
    leaf = _node("技术方案")
    tree = [_node("技术标", depth=0, children=[leaf])]

    assert attach_scope_titles(tree, []) == 0
    assert attach_scope_titles([], [ScopeTitle("智慧园区平台建设")]) == 0
    assert leaf.children == []

"""红线 照搬模式防御测试

用例1: spec 叶子"技术方案"（free_writing）→ _enrich_skeleton_node 被调用，产出子节点 is_skeleton=False
用例2: spec 叶子"投标函"（fixed_template）→ _enrich_skeleton_node 不得被调用
用例3: catchall 骨架子节点在去重中必须被保留
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.writer.outline_generator import OutlineGenerator
from app.schemas.writing import BidDirectorySpec, OutlineNodeGenerated


# ---------------------------------------------------------------------------
# 用例1: free_writing 叶子触发 LLM 丰富，产出子节点为非骨架
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_free_writing_leaf_enriched_with_non_skeleton_children(monkeypatch) -> None:
    """spec 叶子"技术方案"（free_writing）应触发 _enrich_skeleton_node，
    产出的子节点 is_skeleton 必须为 False。"""
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "technical",
                "volume_title": "技术标",
                "items": [
                    {"order": 1, "title": "技术方案"},
                ],
            }
        ],
    )

    enrich_called = False

    async def _stub_enrich(node, *_args, **_kwargs):
        nonlocal enrich_called
        enrich_called = True
        # 模拟真实行为：给节点挂两个非骨架子节点
        child1 = OutlineNodeGenerated(
            title="技术路线", depth=node.depth + 1, sort_order=0,
            node_type="free_writing", is_required=1, score_weight=None,
            children=[], is_skeleton=False, volume_type=node.volume_type,
        )
        child2 = OutlineNodeGenerated(
            title="实施方案", depth=node.depth + 1, sort_order=1,
            node_type="free_writing", is_required=1, score_weight=None,
            children=[], is_skeleton=False, volume_type=node.volume_type,
        )
        node.children = [child1, child2]

    saved_nodes: list = []

    async def _capture_save(_project_id, nodes):
        saved_nodes.extend(nodes)
        return nodes

    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_enrich_skeleton_node", _stub_enrich)
    monkeypatch.setattr(generator, "_save_tree", _capture_save)

    await generator._generate_from_spec(7, spec, 30000)

    assert enrich_called, "_enrich_skeleton_node 应在 free_writing 叶子时被调用"

    vol_node = saved_nodes[0]
    leaf = vol_node.children[0]
    assert leaf.title == "技术方案"
    assert leaf.children, "丰富后叶子应有子节点"
    for child in leaf.children:
        assert not child.is_skeleton, (
            f"丰富产出的子节点 is_skeleton 必须为 False，实际: {child.title}={child.is_skeleton}"
        )


# ---------------------------------------------------------------------------
# 用例2: fixed_template 叶子禁止 LLM 丰富
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fixed_template_leaf_not_enriched(monkeypatch) -> None:
    """spec 叶子"投标函"（fixed_template）不应触发 _enrich_skeleton_node。"""
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "qualification",
                "volume_title": "资质卷",
                "items": [
                    {
                        "order": 1,
                        "title": "投标函",
                        "node_type": "fixed_template",
                        "node_type_source": "llm_suggestion",
                    },
                ],
            }
        ],
    )

    async def _stub_enrich_must_not_be_called(*_args, **_kwargs):
        raise AssertionError("红线: fixed_template 节点不应调用 _enrich_skeleton_node")

    saved_nodes: list = []

    async def _capture_save(_project_id, nodes):
        saved_nodes.extend(nodes)
        return nodes

    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_enrich_skeleton_node", _stub_enrich_must_not_be_called)
    monkeypatch.setattr(generator, "_save_tree", _capture_save)

    await generator._generate_from_spec(7, spec, 30000)

    vol_node = saved_nodes[0]
    leaf = vol_node.children[0]
    assert leaf.title == "投标函"
    assert leaf.children == [], f"fixed_template 叶子不应有子节点，实际: {[c.title for c in leaf.children]}"


# ---------------------------------------------------------------------------
# 用例3: catchall 骨架子节点在兜底去重中必须被保留
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_catchall_skeleton_child_survives_dedup(monkeypatch) -> None:
    """catchall 下来自 spec 的骨架子节点（is_skeleton=True）
    即使与非 catchall 兄弟标题重复，也必须在去重后保留。
    """
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "qualification",
                "volume_title": "资质卷",
                "items": [
                    # 非 catchall 兄弟节点，标题"投标函"
                    {"order": 1, "title": "投标函"},
                    # catchall 节点，含 spec 子节点"投标函"（is_skeleton 将由 _build_skeleton_recursive 设为 True）
                    {
                        "order": 2,
                        "title": "其他资料",
                        "children": [
                            {"order": 1, "title": "投标函"},
                        ],
                    },
                ],
            }
        ],
    )

    saved_nodes: list = []

    async def _capture_save(_project_id, nodes):
        saved_nodes.extend(nodes)
        return nodes

    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_save_tree", _capture_save)

    await generator._generate_from_spec(7, spec, 30000)

    vol_node = saved_nodes[0]
    # 找到 catchall 子节点"其他资料"
    catchall_node = next(
        (c for c in vol_node.children if "其他资料" in c.title), None
    )
    assert catchall_node is not None, "应存在'其他资料'节点"

    skeleton_child_titles = [c.title for c in catchall_node.children if c.is_skeleton]
    assert "投标函" in skeleton_child_titles, (
        f"骨架子节点'投标函'不应被去重移除，当前 catchall 子节点: "
        f"{[c.title for c in catchall_node.children]}"
    )

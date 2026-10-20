"""骨架丰富：并发峰值不超过 get_review_concurrency，子节点集合不变。

同形态双夹具：技术方案 + 实施方案（均为 free_writing 叶子）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.writer.outline_generator import OutlineGenerator
from app.schemas.writing import BidDirectorySpec, OutlineNodeGenerated


@pytest.mark.asyncio
async def test_skeleton_enrich_peak_bounded_and_children_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.config.get_review_concurrency",
        lambda: 2,
    )
    generator = OutlineGenerator(db=SimpleNamespace(), llm_client=SimpleNamespace())
    titles = [
        "技术方案",
        "实施方案",
        "服务方案",
        "项目理解",
        "管理方案",
        "质量保证",
    ]
    spec = BidDirectorySpec(
        has_explicit_directory=True,
        volumes=[
            {
                "volume_type": "technical",
                "volume_title": "技术标",
                "items": [
                    {"order": index, "title": title}
                    for index, title in enumerate(titles, start=1)
                ],
            }
        ],
    )

    current = 0
    peak = 0
    enriched_titles: list[str] = []

    async def _fake_enrich(node, *_args, **_kwargs):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.04)
        current -= 1
        enriched_titles.append(node.title)
        node.children = [
            OutlineNodeGenerated(
                title=f"{node.title}-要点",
                depth=node.depth + 1,
                sort_order=0,
                node_type="free_writing",
                is_required=1,
                score_weight=None,
                children=[],
                is_skeleton=False,
                volume_type=node.volume_type,
            )
        ]

    saved_nodes: list = []

    async def _capture_save(_project_id, nodes):
        saved_nodes.extend(nodes)
        return nodes

    monkeypatch.setattr(generator, "_load_all_confirmed_clauses", AsyncMock(return_value=[]))
    monkeypatch.setattr(generator, "_enrich_skeleton_node", _fake_enrich)
    monkeypatch.setattr(generator, "_save_tree", _capture_save)
    monkeypatch.setattr(
        "app.core.writer.outline_generator.attach_scope_titles_to_outline",
        AsyncMock(return_value=0),
    )

    await generator._generate_from_spec(7, spec, 30000)

    assert peak <= 2
    assert peak >= 2
    assert set(enriched_titles) == set(titles)
    volume = saved_nodes[0]
    leaf_titles = [child.title for child in volume.children]
    assert leaf_titles == titles
    for leaf in volume.children:
        assert [child.title for child in leaf.children] == [f"{leaf.title}-要点"]

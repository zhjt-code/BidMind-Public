""": seed 对存量节点同步 node_class，且 max_tokens 只升不降。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_scripts = Path(__file__).resolve().parents[1] / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from seed_llm_registry import NodeDef, sync_existing_node_runtime  # type: ignore[import-not-found]


def _nodedef(*, node_key: str, tokens: int, node_class: str) -> NodeDef:
    return NodeDef(
        stage_key="material_match",
        flow_key="writing",
        node_key=node_key,
        name=node_key,
        description="",
        builder_key="",
        source_file="",
        execution_channel="review",
        call_method="chat_json",
        default_temperature=0.1,
        default_max_tokens=tokens,
        output_schema_name="",
        visibility="technical",
        editable_level="advanced",
        display_order=0,
        node_class=node_class,
    )


def test_raises_stale_budget_and_aligns_node_class() -> None:
    existing = SimpleNamespace(node_class="judgment", default_max_tokens=2000)
    changed = sync_existing_node_runtime(
        existing, _nodedef(node_key="writing.chapter_judge", tokens=4000, node_class="mechanical"),
    )
    assert changed == ["node_class", "default_max_tokens"]
    assert existing.node_class == "mechanical"
    assert existing.default_max_tokens == 4000


def test_does_not_shrink_higher_admin_budget_second_entity() -> None:
    """同形态第二例: 条款分类存量 8000 不被 seed 降回 4000。"""
    existing = SimpleNamespace(node_class="mechanical", default_max_tokens=8000)
    changed = sync_existing_node_runtime(
        existing, _nodedef(node_key="writing.clause_classify", tokens=4000, node_class="mechanical"),
    )
    assert "default_max_tokens" not in changed
    assert existing.default_max_tokens == 8000
    assert existing.node_class == "mechanical"

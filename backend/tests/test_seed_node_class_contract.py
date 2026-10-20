""": seed 节点必须显式声明 node_class，禁止吃 judgment 默认值。"""

from __future__ import annotations

import sys
from pathlib import Path

_scripts = Path(__file__).resolve().parents[1] / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from seed_llm_registry import ALL_NODES  # type: ignore[import-not-found]


_VALID = frozenset({"mechanical", "judgment"})


def test_all_nodedefs_declare_valid_node_class() -> None:
    missing = [nd.node_key for nd in ALL_NODES if nd.node_class not in _VALID]
    assert missing == [], f"NodeDef 未声明合法 node_class: {missing}"


def test_mandatory_atomic_extract_is_mechanical() -> None:
    matches = [nd for nd in ALL_NODES if nd.node_key == "bid_review.mandatory_atomic_extract"]
    assert len(matches) == 1
    assert matches[0].node_class == "mechanical"


def test_chapter_judge_is_mechanical() -> None:
    """: 章节裁决是结构化分类，走机械池关思考。"""
    matches = [nd for nd in ALL_NODES if nd.node_key == "writing.chapter_judge"]
    assert len(matches) == 1
    assert matches[0].node_class == "mechanical"
    assert matches[0].default_max_tokens == 4000


def test_outline_scope_titles_is_mechanical() -> None:
    """: 同形态第二实体，提取型节点不得默认 judgment。"""
    matches = [nd for nd in ALL_NODES if nd.node_key == "writing.outline_scope_titles"]
    assert len(matches) == 1
    assert matches[0].node_class == "mechanical"


def test_node_class_values_are_only_two_literals() -> None:
    values = {nd.node_class for nd in ALL_NODES}
    assert values <= _VALID
    assert "mechanical" in values
    assert "judgment" in values

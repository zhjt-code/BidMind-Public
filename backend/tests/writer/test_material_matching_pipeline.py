"""Stage D 端到端护栏测试: 素材匹配管线(mock LLM)。

使用 Project 1559 fixture 预演 golden: 期望对出现、forbidden 对不出现。
LLM 以脚本化 mock 按 pair_id 返回裁决，向量召回默认关闭(字面)或注入假 encoder。
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.writer.material_matching.contracts import (
    MaterialMatchingRequest,
)
from app.core.writer.material_matching.pipeline import match_materials_to_chapters

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "material_match_project_1559.json"


def _load_fixture() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _material(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=payload["id"],
        title=payload["title"],
        display_title=None,
        resolved_label=payload.get("resolved_label"),
        label_conflict=payload.get("label_conflict", "uncertain"),
        material_type="text",
        metadata_tags=json.dumps(payload.get("tags", {}), ensure_ascii=False),
        material_role="normal",
        split_review_status="none",
        processing_status="completed",
    )


def _node(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=payload["id"],
        title=payload["title"],
        parent_id=None,
        node_type=None,
        volume_type=None,
        chapter_profile=None,
    )


class _ScriptedLLM:
    """按 pair_id 脚本化返回裁决的 mock；未列出的对默认 unrelated。"""

    def __init__(self, relations: dict[str, str]) -> None:
        self.relations = relations

    async def chat_with_validation(self, *, user_message, response_model, **kwargs):
        pair_ids = re.findall(r'"pair_id":\s*"(m\d+_n\d+)"', user_message)
        decisions = []
        for pair_id in pair_ids:
            relation = self.relations.get(pair_id, "unrelated")
            confidence = 0.9 if relation == "supports" else 0.3
            decisions.append(
                {"pair_id": pair_id, "relation": relation, "confidence": confidence}
            )
        return response_model(decisions=decisions)


def _request() -> MaterialMatchingRequest:
    fixture = _load_fixture()
    return MaterialMatchingRequest(
        project_id=fixture["project_id"],
        materials=[_material(m) for m in fixture["materials"]],
        nodes=[_node(n) for n in fixture["nodes"]],
    )


@pytest.mark.asyncio
async def test_pipeline_binds_expected_drops_forbidden() -> None:
    relations = {
        "m691_n6481": "supports",
        "m691_n6473": "unrelated",
        "m692_n6489": "supports",
        "m692_n6485": "unrelated",
        "m690_n6474": "supports",
        "m690_n6475": "supports",
    }
    proposals = await match_materials_to_chapters(
        _request(), llm_client=_ScriptedLLM(relations), encoder=None
    )
    pairs = {(p.material_id, p.outline_node_id) for p in proposals}

    # 期望绑定出现
    assert (691, 6481) in pairs
    assert (692, 6489) in pairs
    # forbidden 绝不出现
    assert (691, 6473) not in pairs
    assert (692, 6485) not in pairs
    assert (690, 6476) not in pairs


@pytest.mark.asyncio
async def test_pipeline_semantic_misrecall_filtered_by_incomplete_profile() -> None:
    """向量误召回 690->荣誉奖项: 即便 LLM 误判 supports，画像不完整+纯语义仍被丢弃。"""
    fixture = _load_fixture()
    materials = [_material(m) for m in fixture["materials"] if m["id"] == 690]
    nodes = [
        _node(n) for n in fixture["nodes"] if n["id"] in {6474, 6475, 6476}
    ]
    request = MaterialMatchingRequest(project_id=1559, materials=materials, nodes=nodes)

    async def _all_similar_encoder(texts: list[str]) -> np.ndarray:
        # 所有命题向量相同 -> 语义相似度恒为 1，模拟向量误召回把 6476 拉入候选
        return np.array([[1.0, 0.0] for _ in texts], dtype=float)

    # 连 6476 都被 LLM 误判 supports，仍应被决策层的画像门禁挡下
    relations = {"m690_n6474": "supports", "m690_n6475": "supports", "m690_n6476": "supports"}
    proposals = await match_materials_to_chapters(
        request, llm_client=_ScriptedLLM(relations), encoder=_all_similar_encoder
    )
    bound_nodes = {p.outline_node_id for p in proposals}

    assert 6476 not in bound_nodes  # 纯语义 + 画像不完整 -> 丢弃
    assert {6474, 6475} <= bound_nodes  # 有字面旁证 -> 保留

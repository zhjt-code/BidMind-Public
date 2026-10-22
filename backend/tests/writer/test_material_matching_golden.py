"""Project 1559 素材匹配 Golden 测试(护栏)。

使用 backend/tests/writer/fixtures/material_match_project_1559.json 作为最小复现数据，
直接调用 MaterialMatcher._match_materials(委托 material_matching 管线)。

隔离策略:
- patch ``safe_encode`` 为零向量编码器 -> 语义相似度恒为 0，只走确定性字面召回，
 离线可复现、不依赖 bge-m3 模型。
- llm_client 用脚本化 mock: 按 pair_id 返回 supports/unrelated。

目的:
- expected_pairs 必须出现(GREEN)
- forbidden_pairs 必须不出现(修复前为 RED xfail，管线接入后转 GREEN)
- 语义误召回被画像门禁过滤的强化场景见 test_material_matching_pipeline.py
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.core.writer.material_matcher import MaterialMatcher
from app.models.writing_binding import BindingType

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "material_match_project_1559.json"

# fixture 语义: expected 判 supports，其余(含 forbidden)默认 unrelated。
_RELATIONS = {
    "m691_n6481": "supports",
    "m692_n6489": "supports",
    "m690_n6474": "supports",
    "m690_n6475": "supports",
}


def _load_fixture() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _make_material(payload: dict) -> SimpleNamespace:
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


def _make_node(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=payload["id"],
        title=payload["title"],
        embedding=None,
        chapter_profile=None,
        parent_id=None,
        depth=1,
        sort_order=payload["id"],
        volume_type=None,
        node_type=None,
    )


class _ScriptedLLM:
    def __init__(self, relations: dict[str, str]) -> None:
        self.relations = relations

    async def chat_with_validation(self, *, user_message, response_model, **kwargs):
        pair_ids = re.findall(r'"pair_id":\s*"(m\d+_n\d+)"', user_message)
        decisions = []
        for pair_id in pair_ids:
            relation = self.relations.get(pair_id, "unrelated")
            decisions.append(
                {
                    "pair_id": pair_id,
                    "relation": relation,
                    "confidence": 0.9 if relation == "supports" else 0.3,
                }
            )
        return response_model(decisions=decisions)


async def _zero_encoder(texts: list[str], **kwargs) -> np.ndarray:
    """零向量编码器: 语义相似度恒为 0，强制走字面召回。"""
    return np.zeros((len(texts), 8), dtype=float)


def _matcher(relations: dict[str, str]) -> MaterialMatcher:
    db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    return MaterialMatcher(db=db, llm_client=_ScriptedLLM(relations))


async def _run_match(relations: dict[str, str] | None = None) -> list[dict]:
    fixture = _load_fixture()
    matcher = _matcher(relations if relations is not None else _RELATIONS)
    materials = [_make_material(m) for m in fixture["materials"]]
    nodes = [_make_node(n) for n in fixture["nodes"]]
    with patch("app.core.writer.material_matcher.safe_encode", _zero_encoder):
        return await matcher._match_materials([], materials, [], nodes, project_id=1559)


def _bound_nodes(bindings: list[dict], material_id: int) -> set[int]:
    return {
        b["outline_node_id"]
        for b in bindings
        if b["binding_type"] == BindingType.material_to_node and b["material_id"] == material_id
    }


def test_fixture_file_is_readable_and_well_formed() -> None:
    fixture = _load_fixture()
    assert "materials" in fixture
    assert "nodes" in fixture
    assert "expected_pairs" in fixture
    assert "forbidden_pairs" in fixture
    assert len(fixture["materials"]) >= 3
    assert len(fixture["nodes"]) >= 6
    assert len(fixture["forbidden_pairs"]) >= 4


@pytest.mark.asyncio
async def test_material_691_should_bind_to_expected_node_6481() -> None:
    """GREEN: 材料 691(质量管理体系认证证书)应绑定到 6481(企业资质认证)。"""
    bindings = await _run_match()
    assert 6481 in _bound_nodes(bindings, 691)


@pytest.mark.asyncio
async def test_material_692_should_bind_to_expected_node_6489() -> None:
    """GREEN: 材料 692(项目经理职称证书)应绑定到 6489(技术人员资质)。"""
    bindings = await _run_match()
    assert 6489 in _bound_nodes(bindings, 692)


@pytest.mark.asyncio
async def test_material_691_must_not_bind_forbidden_nodes() -> None:
    """GREEN(原 RED): 691 不应绑定 6473(资质证书泛列表)/6512(资质材料汇总)。

 句对裁决把宽泛/汇总章节判为 unrelated，决策层丢弃，不再因字面重合成边。
 """
    bound = _bound_nodes(await _run_match(), 691)
    assert 6473 not in bound
    assert 6512 not in bound


@pytest.mark.asyncio
async def test_material_692_must_not_bind_forbidden_node_6485() -> None:
    """GREEN(原 RED): 692 不应绑定 6485(人员资质证明宽泛节点)。"""
    assert 6485 not in _bound_nodes(await _run_match(), 692)


@pytest.mark.asyncio
async def test_material_690_must_not_bind_forbidden_node_6476() -> None:
    """GREEN(原 RED): 690(企业综合实力)不应绑定 6476(荣誉奖项)。

 690 resolved_label 为空(画像不完整): 6476 无字面命中且语义单路，被召回/决策门禁挡下。
 """
    assert 6476 not in _bound_nodes(await _run_match(), 690)

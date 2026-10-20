"""Stage B 护栏测试: 候选召回。

覆盖:
- 无 encoder 的确定性字面召回，仅召回素材声明章节，不误召回宽泛/无关节点
- 语义召回对"完整命题"整体编码后按阈值召回
- 门禁 block 覆盖高分字面候选
- 每素材 top_k 上限
"""

import numpy as np
import pytest

from app.core.writer.material_matching.contracts import (
    ChapterDemandProfile,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.candidate_recall import recall_candidates


def _mat(**kwargs) -> MaterialSemanticProfile:
    base = dict(material_id=1, title="", material_type="text", label_verified=True)
    base.update(kwargs)
    return MaterialSemanticProfile(**base)


def _chap(node_id: int, title: str, **kwargs) -> ChapterDemandProfile:
    base = dict(node_id=node_id, title=title)
    base.update(kwargs)
    return ChapterDemandProfile(**base)


def _nodes_by_material(candidates, material_id: int) -> set[int]:
    return {c.node_id for c in candidates if c.material_id == material_id}


@pytest.mark.asyncio
async def test_literal_recall_only_declared_sections() -> None:
    material = _mat(
        material_id=691,
        title="质量管理体系认证证书",
        resolved_label="资质证书",
        business_type="qualification_cert",
        applicable_sections=["资质证书", "企业资质"],
    )
    chapters = [
        _chap(6473, "资质证书"),
        _chap(6481, "企业资质认证"),
        _chap(6512, "资质材料汇总"),
        _chap(6476, "荣誉奖项"),
    ]

    candidates = await recall_candidates([material], chapters, encoder=None)
    recalled = _nodes_by_material(candidates, 691)

    assert 6473 in recalled  # applicable 精确命中
    assert 6481 in recalled  # applicable 子串命中
    # 宽泛/无关节点不被字面召回，避免通用证明扩散
    assert 6512 not in recalled
    assert 6476 not in recalled


@pytest.mark.asyncio
async def test_incomplete_profile_material_recalls_declared_not_unrelated() -> None:
    material = _mat(
        material_id=690,
        title="企业综合实力介绍",
        resolved_label=None,
        business_type=None,
        domain="company",
        applicable_sections=["企业概况", "公司简介"],
    )
    chapters = [
        _chap(6474, "企业概况"),
        _chap(6475, "公司简介"),
        _chap(6476, "荣誉奖项"),
    ]

    candidates = await recall_candidates([material], chapters, encoder=None)
    recalled = _nodes_by_material(candidates, 690)

    assert recalled == {6474, 6475}
    assert 6476 not in recalled


@pytest.mark.asyncio
async def test_semantic_recall_with_injected_encoder() -> None:
    material = _mat(material_id=1, business_type=None, proposition_text="素材：服务响应机制")
    related = _chap(10, "无关字面标题甲", semantic_role="x")
    unrelated = _chap(11, "无关字面标题乙", semantic_role="y")
    related.demand_text = "章节：售后服务响应"
    unrelated.demand_text = "章节：财务审计"

    vectors = {
        "素材：服务响应机制": [1.0, 0.0],
        "章节：售后服务响应": [1.0, 0.0],
        "章节：财务审计": [0.0, 1.0],
    }

    async def _encoder(texts: list[str]) -> np.ndarray:
        return np.array([vectors[t] for t in texts], dtype=float)

    candidates = await recall_candidates(
        [material], [related, unrelated], encoder=_encoder, min_similarity=0.55
    )
    recalled = _nodes_by_material(candidates, 1)

    assert 10 in recalled  # 语义高相似
    assert 11 not in recalled  # 语义正交，低于阈值


@pytest.mark.asyncio
async def test_gate_block_overrides_literal_hit() -> None:
    identity = _mat(
        material_id=2,
        business_type="identity_document",
        resolved_label="身份证",
        applicable_sections=["营业执照"],
    )
    chapters = [_chap(20, "营业执照")]

    candidates = await recall_candidates([identity], chapters, encoder=None)
    # 字面完全命中(0.88)但类型跨大类冲突 -> 门禁 block -> 不进候选
    assert _nodes_by_material(candidates, 2) == set()


@pytest.mark.asyncio
async def test_top_k_limits_candidates_per_material() -> None:
    material = _mat(
        material_id=3,
        business_type=None,
        applicable_sections=["通用章节"],
    )
    chapters = [_chap(30 + i, "通用章节") for i in range(6)]

    candidates = await recall_candidates([material], chapters, encoder=None, top_k=3)
    assert len(_nodes_by_material(candidates, 3)) == 3

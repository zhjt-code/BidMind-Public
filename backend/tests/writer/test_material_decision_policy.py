"""Stage D 护栏测试: 绑定决策策略。

覆盖:
- 仅 supports/partial 成边；unrelated/conflict/无裁决丢弃
- partial 标记 needs_review
- 画像不完整时纯语义单路丢弃、有字面旁证放行
- max_nodes 上限与相对分差剪枝
"""

from app.core.writer.material_matching.contracts import (
    MaterialChapterCandidate,
    MaterialPairDecision,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.decision_policy import select_binding_proposals


def _cand(node_id: int, *, material_id: int = 1, score: float = 0.7, sources=None) -> MaterialChapterCandidate:
    return MaterialChapterCandidate(
        material_id=material_id,
        node_id=node_id,
        recall_score=score,
        recall_sources=sources if sources is not None else ["applicable_exact"],
    )


def _decision(node_id: int, relation: str, confidence: float, *, material_id: int = 1) -> MaterialPairDecision:
    return MaterialPairDecision(
        pair_id=f"m{material_id}_n{node_id}", relation=relation, confidence=confidence
    )


def _mat(material_id: int = 1, *, complete: bool = True) -> MaterialSemanticProfile:
    return MaterialSemanticProfile(material_id=material_id, profile_complete=complete)


def _nodes(proposals) -> set[int]:
    return {p.outline_node_id for p in proposals}


def test_only_supports_and_partial_become_edges() -> None:
    candidates = [_cand(10), _cand(11), _cand(12), _cand(13)]
    judge = {
        "m1_n10": _decision(10, "supports", 0.9),
        "m1_n11": _decision(11, "unrelated", 0.8),
        "m1_n12": _decision(12, "conflict", 0.8),
        # n13 无裁决
    }
    proposals = select_binding_proposals(candidates, judge, [_mat()])
    assert _nodes(proposals) == {10}


def test_partial_marked_needs_review() -> None:
    candidates = [_cand(10, score=0.7)]
    judge = {"m1_n10": _decision(10, "partial", 0.7)}
    proposals = select_binding_proposals(candidates, judge, [_mat()])
    assert len(proposals) == 1
    assert proposals[0].needs_review is True


def test_incomplete_profile_semantic_only_dropped() -> None:
    # 画像不完整 + 纯语义单路 -> 丢弃
    semantic_only = [_cand(10, sources=["semantic"])]
    judge = {"m1_n10": _decision(10, "supports", 0.95)}
    dropped = select_binding_proposals(semantic_only, judge, [_mat(complete=False)])
    assert dropped == []

    # 画像不完整 + 字面旁证 -> 放行
    with_literal = [_cand(11, sources=["applicable_exact", "semantic"])]
    judge2 = {"m1_n11": _decision(11, "supports", 0.95)}
    kept = select_binding_proposals(with_literal, judge2, [_mat(complete=False)])
    assert _nodes(kept) == {11}


def test_max_nodes_limit() -> None:
    candidates = [_cand(n) for n in range(10, 15)]
    judge = {f"m1_n{n}": _decision(n, "supports", 0.9 - (n - 10) * 0.01) for n in range(10, 15)}
    proposals = select_binding_proposals(candidates, judge, [_mat()], max_nodes=2)
    assert len(proposals) == 2


def test_default_max_nodes_caps_at_three() -> None:
    # 迁移自 matcher._MAX_MATERIAL_NODES == 3(旧常量已随五路瀑布删除):
    # 不显式传参时，每素材默认最多绑定 3 个章节。
    candidates = [_cand(n) for n in range(10, 15)]
    judge = {f"m1_n{n}": _decision(n, "supports", 0.9) for n in range(10, 15)}
    proposals = select_binding_proposals(candidates, judge, [_mat()])
    assert len(proposals) == 3


def test_relative_margin_prunes_weak_partial() -> None:
    # 一个高分 supports + 一个远低分 partial: partial 应被相对分差剪除
    candidates = [_cand(10, score=0.9), _cand(11, score=0.5)]
    judge = {
        "m1_n10": _decision(10, "supports", 0.9),
        "m1_n11": _decision(11, "partial", 0.5),
    }
    proposals = select_binding_proposals(candidates, judge, [_mat()], relative_margin=0.15)
    assert _nodes(proposals) == {10}

""": 素材候选被质控否决后应留痕告知用户。

这些测试只验证审计提案构建逻辑，不放宽 supports/partial 成边规则。
"""

from app.core.writer.material_matching.contracts import (
    MaterialBindingProposal,
    MaterialChapterCandidate,
    MaterialPairDecision,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.unmatched_audit import (
    build_unmatched_material_proposals,
)


def _material(material_id: int, title: str = "相关业绩.pdf") -> MaterialSemanticProfile:
    return MaterialSemanticProfile(
        material_id=material_id,
        title=title,
        business_type="performance_proof",
        bid_purpose="提供合同扫描件作为业绩证明",
        profile_complete=True,
    )


def test_unmatched_material_keeps_rejection_reason_without_binding_to_node() -> None:
    """unrelated/conflict 仍否决，但最终0绑定素材要产生可见 orphan 留痕。"""
    candidates = [
        MaterialChapterCandidate(
            material_id=701,
            node_id=6590,
            recall_score=0.88,
            recall_sources=["applicable_exact"],
            gate_decision="allow",
            gate_reason="类型无明显冲突",
        )
    ]
    judge_outcome = {
        "m701_n6590": MaterialPairDecision(
            pair_id="m701_n6590",
            relation="unrelated",
            confidence=0.95,
            reason="素材业务领域为压裂液技术试验，章节要求井筒腐蚀类项目，两者业务类型明显不同",
        )
    }

    proposals = build_unmatched_material_proposals(
        materials=[_material(701)],
        candidates=candidates,
        judge_outcome=judge_outcome,
        accepted_proposals=[],
    )

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.material_id == 701
    assert proposal.outline_node_id is None
    assert proposal.needs_review is True
    assert proposal.scope == "orphan"
    assert "AI判定不适用" in proposal.reason
    assert "压裂液技术试验" in proposal.reason


def test_material_with_accepted_binding_does_not_get_unmatched_audit() -> None:
    accepted = [
        MaterialBindingProposal(
            material_id=701,
            outline_node_id=6590,
            confidence=0.9,
            reason="素材正是该章节所需内容",
        )
    ]

    proposals = build_unmatched_material_proposals(
        materials=[_material(701)],
        candidates=[],
        judge_outcome={},
        accepted_proposals=accepted,
    )

    assert proposals == []


def test_unmatched_material_without_candidates_explains_recall_miss() -> None:
    proposals = build_unmatched_material_proposals(
        materials=[_material(696, "HSE证书.jpg")],
        candidates=[],
        judge_outcome={},
        accepted_proposals=[],
    )

    assert len(proposals) == 1
    assert proposals[0].outline_node_id is None
    assert "未召回到合适章节候选" in proposals[0].reason

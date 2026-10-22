""" 章节画像覆盖率根治防御测试。

覆盖:
1. SSOT 三态判定 (matchable/complete/degraded)
2. 分批切分
3. rejects=1 不再丢弃 (matchable 保留)
4. enforce 开启时 75% 覆盖触发补齐
5. enforce 关闭时恢复旧 50% 门控
6. degraded 脱离标题-only demand_text
7. 章节旁证接线: degraded 章节纯语义丢弃
8. 三处口径一致
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.writer.chapter_profiling.batching import (
    compute_coverage,
    find_missing_ids,
    split_leaves_into_batches,
)
from app.core.writer.chapter_profiling.quality import (
    ProfileQuality,
    assess_profile_data,
    assess_profile_raw,
    is_matchable,
)
from app.core.writer.material_matcher import MaterialMatcher
from app.core.writer.material_matching.contracts import (
    ChapterDemandProfile,
    MaterialChapterCandidate,
    MaterialPairDecision,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.decision_policy import select_binding_proposals
from app.core.writer.material_matching.profiles import build_chapter_demand_profiles
from app.core.writer.outline_generator import OutlineGenerator


def test_quality_ssot_three_states() -> None:
    assert assess_profile_data(
        {"semantic_role": "技术", "expects": ["评分"], "rejects": ["报价", "资质"]}
    ) == ProfileQuality.COMPLETE
    assert assess_profile_data(
        {"semantic_role": "技术", "expects": ["评分"], "rejects": ["报价"]}
    ) == ProfileQuality.MATCHABLE
    assert assess_profile_data(
        {"semantic_role": "", "expects": ["评分"], "rejects": ["a", "b"]}
    ) == ProfileQuality.DEGRADED
    assert assess_profile_data(
        {"semantic_role": "技术", "expects": [], "rejects": ["a", "b"]}
    ) == ProfileQuality.DEGRADED
    assert assess_profile_data(None) == ProfileQuality.DEGRADED
    assert is_matchable(ProfileQuality.MATCHABLE) is True
    assert is_matchable(ProfileQuality.COMPLETE) is True
    assert is_matchable(ProfileQuality.DEGRADED) is False


def test_assess_profile_raw_parses_json() -> None:
    raw = '{"semantic_role":"服务","expects":["售后"],"rejects":["报价"]}'
    assert assess_profile_raw(raw) == ProfileQuality.MATCHABLE
    assert assess_profile_raw("") == ProfileQuality.DEGRADED
    assert assess_profile_raw("{bad") == ProfileQuality.DEGRADED


def test_split_leaves_into_batches() -> None:
    leaves = list(range(10))
    batches = split_leaves_into_batches(leaves, 4)
    assert batches == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]
    assert split_leaves_into_batches([], 4) == []
    assert split_leaves_into_batches(leaves, 0) == [leaves]
    assert compute_coverage(expected=4, accepted=3) == 0.75
    assert find_missing_ids([1, 2, 3, 4], {1, 2, 3}) == [4]


def test_accept_keeps_matchable_with_single_reject() -> None:
    """: rejects<2 不再丢弃，只要 role+expects 即 matchable。"""
    profiles = [
        {
            "node_id": 1,
            "semantic_role": "技术方案",
            "expects": ["评分"],
            "rejects": ["报价"],
        },
        {
            "node_id": 2,
            "semantic_role": "",
            "expects": ["x"],
            "rejects": ["a", "b"],
        },
    ]
    results = OutlineGenerator._accept_profile_items(profiles, node_id_set={1, 2})
    assert [nid for nid, _ in results] == [1]
    assert len(results[0][1]["rejects"]) == 1


@pytest.mark.asyncio
async def test_supplement_triggers_at_75_percent_when_enforce_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """: 覆盖率 75%（旧死区）在 enforce 开启时必须补齐。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "WRITING_CHAPTER_PROFILE_ENFORCE_COVERAGE", True)
    og = object.__new__(OutlineGenerator)
    call_counter = {"n": 0}

    async def _fake_call_llm(payload, op_label):
        call_counter["n"] += 1
        assert "补救" in op_label
        return [
            {
                "node_id": 5,
                "semantic_role": "补齐章节",
                "expects": ["资料"],
                "rejects": ["报价", "资质"],
            }
        ]

    missing = [SimpleNamespace(id=5)]
    result = await og._supplement_missing_profiles(
        volume_key="technical",
        volume_label="技术",
        expected=4,  # accepted_before=3 -> 75%
        missing_nodes=missing,
        base_payload={"leaf_list": "..."},
        build_leaf_list=lambda nodes: "stub",
        call_llm=_fake_call_llm,
    )

    assert call_counter["n"] == 1
    assert [nid for nid, _ in result] == [5]


@pytest.mark.asyncio
async def test_supplement_skips_at_75_percent_when_enforce_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兼容回退: enforce=False 时恢复 coverage>=50% 不补救。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "WRITING_CHAPTER_PROFILE_ENFORCE_COVERAGE", False)
    og = object.__new__(OutlineGenerator)
    call_counter = {"n": 0}

    async def _fake_call_llm(payload, op_label):
        call_counter["n"] += 1
        return []

    result = await og._supplement_missing_profiles(
        volume_key="technical",
        volume_label="技术",
        expected=4,
        missing_nodes=[SimpleNamespace(id=5)],
        base_payload={"leaf_list": "..."},
        build_leaf_list=lambda nodes: "stub",
        call_llm=_fake_call_llm,
    )
    assert result == []
    assert call_counter["n"] == 0


def test_degraded_chapter_no_title_only_demand_text() -> None:
    """宁缺毋滥: 无画像章节 demand_text 置空，不再标题-only 静默。"""
    import json

    leaf_ok = SimpleNamespace(
        id=1,
        parent_id=None,
        title="技术方案",
        node_type="free_writing",
        volume_type="technical",
        chapter_profile=json.dumps(
            {
                "semantic_role": "技术方案论述",
                "expects": ["评分标准"],
                "rejects": ["报价", "资质"],
            },
            ensure_ascii=False,
        ),
    )
    leaf_missing = SimpleNamespace(
        id=2,
        parent_id=None,
        title="服务承诺",
        node_type="free_writing",
        volume_type="technical",
        chapter_profile=None,
    )
    profiles = build_chapter_demand_profiles([leaf_ok, leaf_missing])
    by_id = {p.node_id: p for p in profiles}
    assert by_id[1].profile_complete is True
    assert "技术方案" in by_id[1].demand_text
    assert by_id[2].profile_complete is False
    assert by_id[2].demand_text == ""
    assert by_id[2].semantic_role == ""


def test_chapter_degraded_blocks_semantic_only_binding() -> None:
    """章节旁证接线: degraded 章节 + 纯语义 -> 丢弃；有字面旁证放行。"""
    material = MaterialSemanticProfile(material_id=1, profile_complete=True)
    chapter_ok = ChapterDemandProfile(
        node_id=10, title="体系认证", profile_complete=True
    )
    chapter_bad = ChapterDemandProfile(
        node_id=11, title="服务承诺", profile_complete=False
    )

    semantic_to_bad = MaterialChapterCandidate(
        material_id=1, node_id=11, recall_score=0.9, recall_sources=["semantic"]
    )
    literal_to_bad = MaterialChapterCandidate(
        material_id=1,
        node_id=11,
        recall_score=0.88,
        recall_sources=["applicable_exact", "semantic"],
    )
    semantic_to_ok = MaterialChapterCandidate(
        material_id=1, node_id=10, recall_score=0.9, recall_sources=["semantic"]
    )

    judge = {
        "m1_n11": MaterialPairDecision(
            pair_id="m1_n11", relation="supports", confidence=0.95
        ),
        "m1_n10": MaterialPairDecision(
            pair_id="m1_n10", relation="supports", confidence=0.95
        ),
    }

    dropped = select_binding_proposals(
        [semantic_to_bad],
        judge,
        [material],
        chapter_profiles=[chapter_bad],
    )
    assert dropped == []

    kept_literal = select_binding_proposals(
        [literal_to_bad],
        judge,
        [material],
        chapter_profiles=[chapter_bad],
    )
    assert {p.outline_node_id for p in kept_literal} == {11}

    kept_ok = select_binding_proposals(
        [semantic_to_ok],
        judge,
        [material],
        chapter_profiles=[chapter_ok],
    )
    assert {p.outline_node_id for p in kept_ok} == {10}


def test_three_sites_quality_口径_consistent_ssot() -> None:
    """三处消费点对同一画像口径一致: matchable 算 ok，rejects=1 不算 weak。"""
    import json

    matchable_raw = json.dumps(
        {
            "semantic_role": "服务承诺",
            "expects": ["售后"],
            "rejects": ["报价"],
        },
        ensure_ascii=False,
    )
    nodes = [
        SimpleNamespace(id=1, title="A", parent_id=None, chapter_profile=None),
        SimpleNamespace(
            id=2, title="B", parent_id=None, chapter_profile=matchable_raw
        ),
        SimpleNamespace(
            id=3,
            title="C",
            parent_id=None,
            chapter_profile=json.dumps(
                {
                    "semantic_role": "技术",
                    "expects": ["评分"],
                    "rejects": ["报价", "资质"],
                },
                ensure_ascii=False,
            ),
        ),
    ]
    stats = MaterialMatcher._assess_chapter_profile_quality(nodes)
    assert stats["missing"] == 1
    assert stats["ok"] == 2
    assert stats["matchable"] == 1
    assert stats["complete"] == 1
    assert stats["degraded"] == 1
    assert stats["weak"] == 0

    accepted = OutlineGenerator._accept_profile_items(
        [
            {
                "node_id": 2,
                "semantic_role": "服务承诺",
                "expects": ["售后"],
                "rejects": ["报价"],
            }
        ],
        node_id_set={2},
    )
    assert len(accepted) == 1

    demand = build_chapter_demand_profiles(nodes)
    by_id = {p.node_id: p for p in demand}
    assert by_id[2].profile_complete is True
    assert by_id[3].profile_complete is True
    assert by_id[1].profile_complete is False

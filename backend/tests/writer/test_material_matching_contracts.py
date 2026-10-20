"""Stage A 护栏测试: 匹配管线数据契约与语义命题构建。

覆盖:
- pair_id 生成/解析往返一致
- LLM 裁决关系/置信度的鲁棒归一(防止大小写/中文变体导致整批校验失败)
- 素材语义命题拼装、业务类型映射、同义标签归一、未验证图片标记
- 章节需求命题仅取叶子、画像完整性标志
"""

import json
from types import SimpleNamespace

from app.core.writer.material_matching.contracts import (
    MaterialChapterCandidate,
    MaterialPairDecision,
    MaterialPairJudgeBatch,
    make_pair_id,
    parse_pair_id,
)
from app.core.writer.material_matching.profiles import (
    build_chapter_demand_profiles,
    build_material_profiles,
)


def _material(**kwargs):
    base = dict(
        id=1,
        title="",
        display_title=None,
        material_type="text",
        resolved_label=None,
        label_conflict=None,
        metadata_tags=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _node(**kwargs):
    base = dict(
        id=1,
        parent_id=None,
        title="",
        node_type="free_writing",
        volume_type=None,
        chapter_profile=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _tags(**kwargs) -> str:
    payload = {
        "domain": "",
        "bid_purpose": "",
        "entities": [],
        "applicable_sections": [],
        "inapplicable_sections": [],
        "exclusive_section": None,
    }
    payload.update(kwargs)
    return json.dumps(payload, ensure_ascii=False)


def test_pair_id_roundtrip_and_invalid() -> None:
    assert make_pair_id(691, 6481) == "m691_n6481"
    assert parse_pair_id("m691_n6481") == (691, 6481)
    assert parse_pair_id("garbage") is None
    assert parse_pair_id("") is None


def test_pair_decision_relation_normalized() -> None:
    assert MaterialPairDecision(pair_id="m1_n1", relation="支持").relation == "supports"
    assert MaterialPairDecision(pair_id="m1_n1", relation="SUPPORTS").relation == "supports"
    assert MaterialPairDecision(pair_id="m1_n1", relation="冲突").relation == "conflict"
    # 未知关系词保守归为 unrelated(不放行)，而非抛异常使整批失败
    assert MaterialPairDecision(pair_id="m1_n1", relation="不确定xyz").relation == "unrelated"


def test_pair_decision_confidence_clamped() -> None:
    assert MaterialPairDecision(pair_id="m1_n1", relation="supports", confidence=1.7).confidence == 1.0
    assert MaterialPairDecision(pair_id="m1_n1", relation="supports", confidence=-0.5).confidence == 0.0
    assert MaterialPairDecision(pair_id="m1_n1", relation="supports", confidence="abc").confidence == 0.0


def test_candidate_pair_id_autofilled() -> None:
    candidate = MaterialChapterCandidate(material_id=691, node_id=6481)
    assert candidate.pair_id == "m691_n6481"


def test_judge_batch_parses_dict_decisions() -> None:
    batch = MaterialPairJudgeBatch(
        decisions=[
            {"pair_id": "m1_n1", "relation": "supports", "confidence": 0.9},
            {"pair_id": "m1_n2", "relation": "无关", "confidence": 0.2},
        ]
    )
    assert len(batch.decisions) == 2
    assert batch.decisions[1].relation == "unrelated"


def test_material_profile_composes_proposition_and_business_type() -> None:
    material = _material(
        id=691,
        title="三体系确认证书",
        material_type="image",
        resolved_label="三体系认证",
        label_conflict="matched",
        metadata_tags=_tags(
            domain="企业资质",
            bid_purpose="企业质量管理体系认证证明",
            entities=["ISO9001", "质量管理体系"],
            applicable_sections=["资质证明"],
        ),
    )
    profile = build_material_profiles([material])[0]

    assert profile.business_type == "qualification_cert"
    assert "业务类型：三体系认证" in profile.proposition_text
    assert "企业质量管理体系认证证明" in profile.proposition_text
    assert "ISO9001" in profile.proposition_text
    assert profile.profile_complete is True
    assert profile.label_verified is True


def test_synonym_labels_share_business_type_but_subjects_split() -> None:
    iso = build_material_profiles([_material(resolved_label="ISO认证")])[0]
    three = build_material_profiles([_material(resolved_label="三体系认证")])[0]
    # 体系认证家族同大类，避免被判冲突(同义归一)
    assert iso.business_type == three.business_type == "qualification_cert"

    identity = build_material_profiles([_material(resolved_label="身份证")])[0]
    license_ = build_material_profiles([_material(resolved_label="营业执照")])[0]
    # 证件主体不同必须分属不同大类，防止身份证↔营业执照串写
    assert identity.business_type == "identity_document"
    assert license_.business_type == "business_license"
    assert identity.business_type != license_.business_type


def test_performance_synonym_labels_share_canonical_group() -> None:
    """业绩材料与经营业绩是同一业务族，细标签归一不应误判冲突。"""
    from app.core.writer.material_matching.material_taxonomy import same_label_group

    assert same_label_group("业绩材料", "经营业绩") is True
    assert same_label_group("项目业绩", "业绩证明") is True
    # 项目合同可能属于非业绩合同，不能仅按细标签归入同义组。
    assert same_label_group("项目合同", "业绩材料") is False


def test_material_profile_keeps_business_type_when_label_sources_share_big_class() -> None:
    """细标签冲突但业务大类一致时，画像应保留大类共识供匹配审计使用。"""
    profile = build_material_profiles(
        [
            _material(
                title="相关业绩.pdf",
                file_name_label="业绩材料",
                content_label="项目合同",
                label_conflict="needs_review",
                metadata_tags=_tags(
                    domain="项目业绩",
                    bid_purpose="提供类似项目合同扫描件作为业绩证明",
                    applicable_sections=["近三年相关项目合同扫描件"],
                ),
            )
        ]
    )[0]

    assert profile.resolved_label is None
    assert profile.business_type == "performance_proof"
    assert profile.profile_complete is True
    assert "业务类型：performance_proof" in profile.proposition_text


def test_unverified_image_marked_not_verified() -> None:
    unverified = build_material_profiles(
        [_material(material_type="image", label_conflict="needs_review")]
    )[0]
    verified = build_material_profiles(
        [_material(material_type="image", label_conflict="matched")]
    )[0]
    text_material = build_material_profiles([_material(material_type="text")])[0]

    assert unverified.label_verified is False
    assert verified.label_verified is True
    assert text_material.label_verified is True


def test_incomplete_material_profile_flagged() -> None:
    # 无业务类型真源、无标签 -> 画像不完整，禁止语义单路放行
    profile = build_material_profiles([_material(title="扫描件")])[0]
    assert profile.profile_complete is False


def test_chapter_demand_only_leaf_with_text() -> None:
    parent = _node(id=100, parent_id=None, title="资质证明")
    leaf_a = _node(
        id=101,
        parent_id=100,
        title="质量管理体系认证",
        chapter_profile=json.dumps(
            {
                "semantic_role": "展示企业质量/环境/职业健康体系认证",
                "expects": ["体系认证证书", "ISO认证"],
                "rejects": ["投标保证金", "开户许可证"],
            },
            ensure_ascii=False,
        ),
    )
    leaf_b = _node(id=102, parent_id=100, title="投标保证金", chapter_profile=None)

    profiles = build_chapter_demand_profiles([parent, leaf_a, leaf_b])
    by_id = {p.node_id: p for p in profiles}

    # 父节点不产出画像
    assert 100 not in by_id
    assert set(by_id.keys()) == {101, 102}

    profile_a = by_id[101]
    assert "功能定位：展示企业质量" in profile_a.demand_text
    assert "ISO认证" in profile_a.demand_text
    assert "投标保证金" in profile_a.demand_text  # 出现在 rejects 段
    assert profile_a.profile_complete is True

    # 无画像章节: 宁缺毋滥，demand_text 置空，完整性为 False
    assert by_id[102].profile_complete is False
    assert by_id[102].demand_text == ""
    assert by_id[102].semantic_role == ""

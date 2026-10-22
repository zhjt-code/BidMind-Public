import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.scoring


def _valid_draft_payload() -> dict:
    return {
        "section_goal": "响应技术方案评分要求",
        "arguments": [{"argument_id": "arg_1", "claim": "建立分阶段实施路径"}],
        "measures": [{
            "measure_id": "measure_1",
            "action": "设置里程碑",
            "steps": ["准备", "执行"],
            "verification": "按节点验收",
        }],
        "acceptance_criteria": [{
            "criterion_id": "acc_1",
            "criterion": "完成验收",
            "verification_method": "检查记录",
        }],
    }


def _request_with_sources():
    from app.core.writer.section_plan.contracts import (
        SectionPlanConstraintInput,
        SectionPlanEvidenceCandidate,
        SectionPlanRequest,
    )

    return SectionPlanRequest(
        project_id=2120,
        outline_node_id=7241,
        node_title="其他资料",
        target_word_count=1000,
        constraints=[
            SectionPlanConstraintInput(
                clause_id=18066,
                sub_type="scoring_criterion",
                content="投标书规范、内容完整得分，满分2分",
                score_weight=2,
            ),
            SectionPlanConstraintInput(
                clause_id=17911,
                sub_type="content_required",
                content="自合同生效之日起90天内完成测试并提交报告",
            ),
        ],
        evidence_catalog=[
            SectionPlanEvidenceCandidate(
                evidence_key="clause:18066",
                source_type="clause",
                source_id=18066,
                excerpt="投标书规范、内容完整得分，满分2分",
                content_sha256="a" * 64,
                fact_eligible=True,
            ),
            SectionPlanEvidenceCandidate(
                evidence_key="clause:17911",
                source_type="clause",
                source_id=17911,
                excerpt="自合同生效之日起90天内完成测试并提交报告",
                content_sha256="b" * 64,
                fact_eligible=True,
            ),
        ],
        prompt_snapshot={"planner_prompt_sha256": "c" * 64, "source": "code"},
        input_fingerprint="d" * 64,
    )


def test_section_plan_draft_unwraps_single_key_envelope() -> None:
    """: LLM 常把结果包在 {"section_plan": {...}} 单键信封里, 必须解包成功。"""
    from app.core.writer.section_plan.contracts import SectionPlanDraft

    draft = SectionPlanDraft.model_validate({"section_plan": _valid_draft_payload()})

    assert draft.section_goal == "响应技术方案评分要求"
    assert draft.measures[0].steps == ["准备", "执行"]


def test_measure_steps_accepts_single_string_and_coerces_to_list() -> None:
    """: LLM 偶发把 steps 输出为字符串, 需归一为单元素列表。"""
    from app.core.writer.section_plan.contracts import SectionPlanDraft

    payload = _valid_draft_payload()
    payload["measures"][0]["steps"] = "逐页编码，并检查编码连续性。"

    draft = SectionPlanDraft.model_validate(payload)

    assert draft.measures[0].steps == ["逐页编码，并检查编码连续性。"]


def test_section_plan_system_prompt_embeds_output_field_schema() -> None:
    """: chat_with_validation 不注入 schema, Prompt 必须自带逐字段输出模板。"""
    from app.core.writer.prompts.section_plan import SECTION_PLAN_SYSTEM

    required_field_names = [
        "section_goal",
        "scoring_responses",
        "arguments",
        "argument_id",
        "claim",
        "measures",
        "measure_id",
        "action",
        "steps",
        "verification",
        "acceptance_criteria",
        "criterion_id",
        "criterion",
        "verification_method",
        "missing_information",
    ]
    for field_name in required_field_names:
        assert field_name in SECTION_PLAN_SYSTEM, f"missing field in prompt: {field_name}"
    assert "顶层" in SECTION_PLAN_SYSTEM
    assert "section_plan" in SECTION_PLAN_SYSTEM  # 明示禁止的包裹键名


def test_section_plan_draft_rejects_extra_fields() -> None:
    from app.core.writer.section_plan.contracts import SectionPlanDraft

    with pytest.raises(ValidationError):
        SectionPlanDraft(
            section_goal="响应技术评分要求",
            arguments=[{"argument_id": "arg_1", "claim": "建立分阶段实施路径"}],
            measures=[{"measure_id": "measure_1", "action": "设置里程碑", "steps": ["准备"], "verification": "按节点验收"}],
            acceptance_criteria=[{"criterion_id": "acc_1", "criterion": "完成验收", "verification_method": "检查记录"}],
            invented_field=True,
        )


def test_validate_section_plan_rejects_unknown_evidence_key() -> None:
    from app.core.writer.section_plan.contracts import (
        SectionPlanDraft,
        SectionPlanEvidenceCandidate,
        SectionPlanRequest,
        validate_and_materialize_section_plan,
    )

    request = SectionPlanRequest(
        project_id=1,
        outline_node_id=2,
        node_title="技术方案",
        target_word_count=1000,
        constraints=[],
        evidence_catalog=[
            SectionPlanEvidenceCandidate(
                evidence_key="clause:1",
                source_type="clause",
                source_id=1,
                excerpt="工期为30日历天",
                content_sha256="a" * 64,
                fact_eligible=True,
            )
        ],
        prompt_snapshot={"planner_prompt_sha256": "b" * 64, "source": "code"},
        input_fingerprint="c" * 64,
    )
    draft = SectionPlanDraft(
        section_goal="响应技术方案要求",
        project_facts=[{"fact_id": "fact_1", "evidence_key": "clause:999", "intended_use": "说明工期"}],
        arguments=[{"argument_id": "arg_1", "claim": "按工期组织实施", "project_fact_ids": ["fact_1"]}],
        measures=[{"measure_id": "measure_1", "action": "编制计划", "steps": ["准备"], "verification": "进度检查"}],
        acceptance_criteria=[{"criterion_id": "acc_1", "criterion": "按期完成", "verification_method": "验收"}],
    )

    with pytest.raises(ValueError, match="unknown evidence_key"):
        validate_and_materialize_section_plan(draft, request)


def test_validate_section_plan_removes_project_fact_from_ineligible_evidence() -> None:
    from app.core.writer.section_plan.contracts import (
        SectionPlanDraft,
        SectionPlanEvidenceCandidate,
        SectionPlanRequest,
        validate_and_materialize_section_plan,
    )

    request = SectionPlanRequest(
        project_id=1,
        outline_node_id=2,
        node_title="技术方案",
        target_word_count=1000,
        constraints=[],
        evidence_catalog=[
            SectionPlanEvidenceCandidate(
                evidence_key="industry:1",
                source_type="industry_profile",
                source_id=1,
                excerpt="建议采用分阶段实施语言",
                content_sha256="a" * 64,
                fact_eligible=False,
            )
        ],
        prompt_snapshot={"planner_prompt_sha256": "b" * 64, "source": "code"},
        input_fingerprint="c" * 64,
    )
    draft = SectionPlanDraft(
        section_goal="响应技术方案要求",
        project_facts=[{"fact_id": "fact_1", "evidence_key": "industry:1", "intended_use": "作为项目事实"}],
        arguments=[{"argument_id": "arg_1", "claim": "分阶段实施", "project_fact_ids": ["fact_1"]}],
        measures=[{"measure_id": "measure_1", "action": "编制计划", "steps": ["准备"], "verification": "进度检查"}],
        acceptance_criteria=[{"criterion_id": "acc_1", "criterion": "按期完成", "verification_method": "验收"}],
    )

    materialized = validate_and_materialize_section_plan(draft, request)

    assert materialized["project_facts"] == []
    assert materialized["arguments"][0]["project_fact_ids"] == []
    assert materialized["safety_patches"][0]["kind"] == "ineligible_project_fact"


def test_materialize_auto_links_scoring_response_to_clause_evidence() -> None:
    """: scoring response 即使漏写 evidence_keys, 也应确定性绑定 clause:{id}。"""
    from app.core.writer.section_plan.contracts import (
        SectionPlanDraft,
        validate_and_materialize_section_plan,
    )

    payload = _valid_draft_payload()
    payload["scoring_responses"] = [{
        "scoring_point_id": "sp_1",
        "clause_id": 18066,
        "response_actions": ["逐条响应标书完整性评分要求"],
        "evidence_keys": [],
    }]
    draft = SectionPlanDraft.model_validate(payload)

    materialized = validate_and_materialize_section_plan(
        draft,
        _request_with_sources(),
    )

    assert materialized["scoring_responses"][0]["evidence_keys"] == ["clause:18066"]


def test_materialize_rejects_scoring_response_with_unknown_clause_id() -> None:
    from app.core.writer.section_plan.contracts import (
        SectionPlanDraft,
        validate_and_materialize_section_plan,
    )

    payload = _valid_draft_payload()
    payload["scoring_responses"] = [{
        "scoring_point_id": "sp_1",
        "clause_id": 99999,
        "response_actions": ["响应不存在的评分项"],
        "evidence_keys": [],
    }]
    draft = SectionPlanDraft.model_validate(payload)

    with pytest.raises(ValueError, match="unknown scoring clause_id"):
        validate_and_materialize_section_plan(draft, _request_with_sources())


def test_materialize_accepts_score_weighted_hard_constraint_as_scoring_item() -> None:
    """: 带明确正分值的硬约束同样是评分事实，不能只认sub_type。"""
    from app.core.writer.section_plan.contracts import (
        SectionPlanConstraintInput,
        SectionPlanDraft,
        SectionPlanEvidenceCandidate,
        SectionPlanRequest,
        validate_and_materialize_section_plan,
    )

    request = SectionPlanRequest(
        project_id=2119,
        outline_node_id=7205,
        node_title="投标文件完整性与规范性自评",
        target_word_count=500,
        constraints=[
            SectionPlanConstraintInput(
                clause_id=17741,
                sub_type="disqualification",
                content="投标文件规范、内容完整得2分",
                score_weight=2,
            ),
        ],
        evidence_catalog=[
            SectionPlanEvidenceCandidate(
                evidence_key="clause:17741",
                source_type="clause",
                source_id=17741,
                excerpt="投标文件规范、内容完整得2分",
                content_sha256="f" * 64,
                fact_eligible=True,
            ),
        ],
        prompt_snapshot={"planner_prompt_sha256": "c" * 64, "source": "code"},
        input_fingerprint="d" * 64,
    )
    payload = _valid_draft_payload()
    payload["scoring_responses"] = [{
        "scoring_point_id": "sp_1",
        "clause_id": 17741,
        "response_actions": ["逐项自评投标文件完整性"],
        "evidence_keys": [],
    }]
    draft = SectionPlanDraft.model_validate(payload)

    materialized = validate_and_materialize_section_plan(draft, request)

    assert materialized["scoring_responses"][0]["evidence_keys"] == [
        "clause:17741",
    ]


def test_materialize_accepts_sourced_numeric_commitment() -> None:
    from app.core.writer.section_plan.contracts import (
        SectionPlanDraft,
        validate_and_materialize_section_plan,
    )

    payload = _valid_draft_payload()
    payload["measures"][0]["action"] = "确保90天内完成测试"
    payload["acceptance_criteria"][0]["criterion"] = "90天内提交报告"
    draft = SectionPlanDraft.model_validate(payload)

    materialized = validate_and_materialize_section_plan(
        draft,
        _request_with_sources(),
    )

    assert materialized["measures"][0]["action"] == "确保90天内完成测试"


def test_materialize_sanitizes_unsourced_numeric_commitment() -> None:
    from app.core.writer.section_plan.contracts import (
        SectionPlanDraft,
        validate_and_materialize_section_plan,
    )

    payload = _valid_draft_payload()
    payload["measures"][0]["action"] = "承诺120天内完成测试"
    draft = SectionPlanDraft.model_validate(payload)

    materialized = validate_and_materialize_section_plan(
        draft,
        _request_with_sources(),
    )

    assert materialized["measures"][0]["action"] == "承诺[待补充:核验数值]内完成测试"
    assert materialized["safety_patches"][0]["kind"] == "unsupported_numeric_claim"
    assert materialized["safety_patches"][0]["original"] == "120天"


def test_materialize_sanitizes_unsourced_commitment_document() -> None:
    from app.core.writer.section_plan.contracts import (
        SectionPlanDraft,
        validate_and_materialize_section_plan,
    )

    payload = _valid_draft_payload()
    payload["measures"][0]["action"] = "附上项目负责人签字确认的工期承诺函"
    draft = SectionPlanDraft.model_validate(payload)

    materialized = validate_and_materialize_section_plan(
        draft,
        _request_with_sources(),
    )

    assert "[待补充:核验材料]" in materialized["measures"][0]["action"]
    assert "承诺函" not in materialized["measures"][0]["action"]
    assert materialized["safety_patches"][0]["kind"] == "unsupported_commitment_document"


def test_materialize_sanitizes_unsourced_term_when_later_term_is_sourced() -> None:
    """混合承诺词中，后一个有来源词不能让前一个无来源词逃逸。"""
    from app.core.writer.section_plan.contracts import (
        SectionPlanConstraintInput,
        SectionPlanDraft,
        SectionPlanEvidenceCandidate,
        SectionPlanRequest,
        validate_and_materialize_section_plan,
    )

    request = SectionPlanRequest(
        project_id=1,
        outline_node_id=2,
        node_title="证明材料",
        target_word_count=500,
        constraints=[
            SectionPlanConstraintInput(
                clause_id=1,
                sub_type="material_required",
                content="需提供相关证明材料",
            ),
        ],
        evidence_catalog=[
            SectionPlanEvidenceCandidate(
                evidence_key="clause:1",
                source_type="clause",
                source_id=1,
                excerpt="需提供相关证明材料",
                content_sha256="a" * 64,
                fact_eligible=True,
            ),
        ],
        prompt_snapshot={"planner_prompt_sha256": "b" * 64, "source": "code"},
        input_fingerprint="c" * 64,
    )
    payload = _valid_draft_payload()
    payload["measures"][0]["action"] = "提交承诺函并提供证明材料"
    draft = SectionPlanDraft.model_validate(payload)

    materialized = validate_and_materialize_section_plan(draft, request)

    action = materialized["measures"][0]["action"]
    assert "承诺函" not in action
    assert "[待补充:核验材料]" in action
    assert "证明材料" in action


def test_materialize_numeric_unit_matching_is_case_insensitive() -> None:
    """技术单位由正则忽略大小写匹配时，source 对照也必须忽略大小写。"""
    from app.core.writer.section_plan.contracts import (
        SectionPlanConstraintInput,
        SectionPlanDraft,
        SectionPlanEvidenceCandidate,
        SectionPlanRequest,
        validate_and_materialize_section_plan,
    )

    base = _request_with_sources().model_dump(mode="json")
    base["constraints"].append(
        SectionPlanConstraintInput(
            clause_id=18888,
            sub_type="content_required",
            content="试验压力为30MPa",
        ).model_dump(mode="json")
    )
    base["evidence_catalog"].append(
        SectionPlanEvidenceCandidate(
            evidence_key="clause:18888",
            source_type="clause",
            source_id=18888,
            excerpt="试验压力为30MPa",
            content_sha256="e" * 64,
            fact_eligible=True,
        ).model_dump(mode="json")
    )
    request = SectionPlanRequest.model_validate(base)
    payload = _valid_draft_payload()
    payload["measures"][0]["action"] = "将试验压力控制为30mpa"
    draft = SectionPlanDraft.model_validate(payload)

    materialized = validate_and_materialize_section_plan(draft, request)

    assert materialized["measures"][0]["action"] == "将试验压力控制为30mpa"

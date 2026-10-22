from types import SimpleNamespace


def test_build_section_plan_request_accepts_orm_clause_with_sqlalchemy_metadata() -> None:
    """: ORM 模型的 metadata 属性是 SQLAlchemy MetaData 注册表, 必须走 metadata_json。"""
    from app.core.writer.section_plan.context import build_section_plan_request
    from app.models.writing_clause import WritingClause, WritingClauseLayer

    clause = WritingClause(
        id=11,
        project_id=2119,
        document_id=1,
        layer=WritingClauseLayer.generation_guide,
        sub_type="scoring_criterion",
        content="技术方案完整性 20 分",
        score_weight=20,
        metadata_json={"score_rules": ["方案完整得20分"]},
    )
    node = SimpleNamespace(id=2, title="技术方案", target_word_count=1200, chapter_profile=None)

    request = build_section_plan_request(
        project_id=2119,
        node=node,
        constraints=[clause],
        material_entries=[],
        industry_profile_text="",
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
    )

    assert request.constraints[0].clause_id == 11
    assert request.constraints[0].metadata == {"score_rules": ["方案完整得20分"]}
    assert [item.evidence_key for item in request.evidence_catalog] == ["clause:11"]
    assert request.evidence_catalog[0].source_type == "clause"
    assert request.evidence_catalog[0].source_id == 11
    assert request.evidence_catalog[0].excerpt == "技术方案完整性 20 分"
    assert request.evidence_catalog[0].fact_eligible is True


def test_build_section_plan_request_clamps_inputs_to_contract_bounds() -> None:
    """契约越界输入(超长内容/画像/字数/空素材)必须在构建层收敛, 不得整体降级。"""
    from app.core.writer.section_plan.context import build_section_plan_request

    node = SimpleNamespace(
        id=3,
        title="服务方案",
        target_word_count=12000,
        chapter_profile={"semantic_role": "x" * 3000},
    )
    request = build_section_plan_request(
        project_id=1,
        node=node,
        constraints=[
            SimpleNamespace(id=1, sub_type="content_required", content="长" * 2600, score_weight=-1, metadata_json='{"k":"v"}'),
            SimpleNamespace(id=2, sub_type="content_required", content="   ", score_weight=None, metadata_json=None),
        ],
        material_entries=[
            {"evidence_key": "material:1", "source_id": 1, "excerpt": "", "fact_eligible": True},
            {"evidence_key": "material:2", "source_id": 2, "excerpt": "摘" * 700, "fact_eligible": True},
        ],
        industry_profile_text="业" * 5000,
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
    )

    assert request.target_word_count == 5000
    assert len(request.industry_profile_text) == 4000
    assert len(request.chapter_profile_text) <= 2000
    assert [c.clause_id for c in request.constraints] == [1]
    assert len(request.constraints[0].content) == 2000
    assert request.constraints[0].score_weight is None
    assert request.constraints[0].metadata == {"k": "v"}
    assert [e.evidence_key for e in request.evidence_catalog] == [
        "clause:1",
        "material:2",
    ]
    assert len(request.evidence_catalog[1].excerpt) == 600


def test_build_section_plan_request_registers_every_constraint_as_clause_evidence() -> None:
    """: planner 可见的每条约束必须有同源 clause evidence key。"""
    from app.core.writer.section_plan.context import build_section_plan_request

    node = SimpleNamespace(id=7241, title="其他资料", target_word_count=1000, chapter_profile=None)
    request = build_section_plan_request(
        project_id=2120,
        node=node,
        constraints=[
            SimpleNamespace(
                id=18066,
                sub_type="scoring_criterion",
                content="投标书规范、内容完整得2分",
                score_weight=2,
                metadata_json={"score_item_name": "标书制作"},
            ),
            SimpleNamespace(
                id=17884,
                sub_type="disqualification",
                content="必须使用招标文件提供的全部格式",
                score_weight=None,
                metadata_json=None,
            ),
        ],
        material_entries=[
            {
                "evidence_key": "material:818",
                "source_id": 818,
                "excerpt": "质量管理体系证书",
                "fact_eligible": True,
            }
        ],
        industry_profile_text="",
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
    )

    evidence_by_key = {item.evidence_key: item for item in request.evidence_catalog}
    assert set(evidence_by_key) == {"clause:17884", "clause:18066", "material:818"}
    assert evidence_by_key["clause:18066"].source_id == 18066
    assert evidence_by_key["clause:18066"].fact_eligible is True
    assert evidence_by_key["clause:17884"].fact_eligible is True


def test_non_factual_clause_evidence_is_not_fact_eligible() -> None:
    from app.core.writer.section_plan.context import build_section_plan_request

    request = build_section_plan_request(
        project_id=1,
        node=SimpleNamespace(id=3, title="技术方案", target_word_count=1000, chapter_profile=None),
        constraints=[
            SimpleNamespace(
                id=10,
                sub_type="industry_profile",
                content="建议采用行业专业表达",
                score_weight=None,
                metadata_json=None,
            ),
        ],
        material_entries=[],
        industry_profile_text="",
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
    )

    assert request.evidence_catalog[0].evidence_key == "clause:10"
    assert request.evidence_catalog[0].fact_eligible is False


def test_direct_emphasis_clause_evidence_is_fact_eligible() -> None:
    """: confirmed/direct 强调要求是招标事实，不能按 subtype 粗暴排除。"""
    from app.core.writer.section_plan.context import build_section_plan_request

    request = build_section_plan_request(
        project_id=2120,
        node=SimpleNamespace(id=7222, title="公司简介", target_word_count=1000, chapter_profile=None),
        constraints=[
            SimpleNamespace(
                id=17995,
                sub_type="emphasis_point",
                content="投标人需具备科研服务专业准入及履约能力",
                score_weight=None,
                metadata_json={"writing_relevance": "direct"},
            ),
        ],
        material_entries=[],
        industry_profile_text="",
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
    )

    assert request.evidence_catalog[0].evidence_key == "clause:17995"
    assert request.evidence_catalog[0].fact_eligible is True


def test_evidence_catalog_truncation_keeps_clause_and_warns(monkeypatch) -> None:
    """证据槽位超限时必须保留 clause evidence 并输出可观测告警。"""
    import app.core.writer.section_plan.context as context

    warnings: list[tuple] = []
    monkeypatch.setattr(
        context.logger,
        "warning",
        lambda *args: warnings.append(args),
    )
    request = context.build_section_plan_request(
        project_id=1,
        node=SimpleNamespace(id=3, title="技术方案", target_word_count=1000, chapter_profile=None),
        constraints=[
            SimpleNamespace(
                id=10,
                sub_type="scoring_criterion",
                content="方案完整得10分",
                score_weight=10,
                metadata_json=None,
            ),
        ],
        material_entries=[
            {
                "evidence_key": f"material:{index}",
                "source_id": index,
                "excerpt": f"素材{index}",
                "fact_eligible": True,
            }
            for index in range(1, 101)
        ],
        industry_profile_text="",
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
    )

    assert len(request.evidence_catalog) == 100
    assert request.evidence_catalog[0].evidence_key == "clause:10"
    assert warnings


def test_build_section_plan_request_fingerprint_ignores_input_order() -> None:
    from app.core.writer.section_plan.context import build_section_plan_request

    node = SimpleNamespace(id=2, title="技术方案", target_word_count=1200, chapter_profile=None)
    first = build_section_plan_request(
        project_id=1,
        node=node,
        constraints=[
            SimpleNamespace(id=2, sub_type="content_required", content="B", score_weight=1, metadata_json=None),
            SimpleNamespace(id=1, sub_type="scoring_criterion", content="A", score_weight=5, metadata_json=None),
        ],
        material_entries=[
            {"evidence_key": "material:2", "source_id": 2, "excerpt": "设备清单", "fact_eligible": True},
            {"evidence_key": "material:1", "source_id": 1, "excerpt": "项目业绩", "fact_eligible": True},
        ],
        industry_profile_text="行业画像",
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
    )
    second = build_section_plan_request(
        project_id=1,
        node=node,
        constraints=list(reversed(first.constraints)),
        material_entries=list(reversed(first.evidence_catalog)),
        industry_profile_text="行业画像",
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
    )

    assert first.input_fingerprint == second.input_fingerprint
    assert first.input_fingerprint != "a" * 64


def test_build_section_plan_request_does_not_include_previous_summaries() -> None:
    from app.core.writer.section_plan.context import build_section_plan_request

    node = SimpleNamespace(id=2, title="技术方案", target_word_count=1200, chapter_profile=None)
    base = build_section_plan_request(
        project_id=1,
        node=node,
        constraints=[],
        material_entries=[],
        industry_profile_text="",
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
        previous_summaries=["旧摘要"],
    )
    changed = build_section_plan_request(
        project_id=1,
        node=node,
        constraints=[],
        material_entries=[],
        industry_profile_text="",
        prompt_snapshot={"planner_prompt_sha256": "a" * 64, "source": "code"},
        previous_summaries=["新摘要"],
    )

    assert base.input_fingerprint == changed.input_fingerprint

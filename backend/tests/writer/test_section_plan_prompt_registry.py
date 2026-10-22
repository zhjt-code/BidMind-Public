def test_section_plan_is_deterministic_and_in_writing_snapshot() -> None:
    from app.core.ai.prompt_registry import FLOW_NODE_KEYS, _derive_stability_class

    assert _derive_stability_class("writing.section_plan") == "deterministic"
    assert "writing.section_plan" in FLOW_NODE_KEYS["writing"]


def test_get_section_plan_prompt_prefers_registry_templates(monkeypatch) -> None:
    """: planner Prompt 必须 Registry 优先, LLM Center 在线编辑才可生效。"""
    import app.core.ai.prompt_registry as reg
    from app.core.writer.prompts.section_plan import get_section_plan_prompt

    monkeypatch.setattr(
        reg,
        "resolve_system_prompt",
        lambda node, default: "SYS_REG {{json}}" if node == "writing.section_plan" else default,
    )
    monkeypatch.setattr(
        reg,
        "resolve_user_template",
        lambda node, default: (
            "USER_REG {evidence_catalog_json}" if node == "writing.section_plan" else default
        ),
    )

    system, user = get_section_plan_prompt(
        node_context_json="{}",
        chapter_profile_text="p",
        industry_profile_text="i",
        constraints_json="[]",
        scoring_points_json="[]",
        evidence_catalog_json="[EV]",
    )

    assert system == "SYS_REG {json}"
    assert user == "USER_REG [EV]"


def test_get_section_plan_prompt_falls_back_when_registry_template_broken(monkeypatch) -> None:
    """Registry 模板占位符损坏时必须回退代码默认, 不得让 planner 崩溃。"""
    import app.core.ai.prompt_registry as reg
    from app.core.writer.prompts.section_plan import get_section_plan_prompt

    monkeypatch.setattr(
        reg,
        "resolve_user_template",
        lambda node, default: "USER_BROKEN {nope}" if node == "writing.section_plan" else default,
    )

    _, user = get_section_plan_prompt(
        node_context_json="{}",
        chapter_profile_text="p",
        industry_profile_text="i",
        constraints_json="[]",
        scoring_points_json="[]",
        evidence_catalog_json="[EV]",
    )

    assert "[EV]" in user
    assert "USER_BROKEN" not in user


def test_section_plan_prompt_contract_declares_required_placeholders() -> None:
    from app.core.ai.prompt_contracts import get_required_placeholders

    assert get_required_placeholders("writing.section_plan", "user") == {
        "node_context_json",
        "chapter_profile_text",
        "industry_profile_text",
        "constraints_json",
        "scoring_points_json",
        "evidence_catalog_json",
    }

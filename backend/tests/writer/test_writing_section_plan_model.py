from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint


def test_writing_section_plan_model_declares_versioning_constraints() -> None:
    from app.models.writing_section_plan import WritingSectionPlan

    table = WritingSectionPlan.__table__

    assert table.name == "writing_section_plans"
    assert "input_fingerprint" in table.c
    assert "plan_payload" in table.c
    assert "prompt_snapshot" in table.c
    assert "generation_token" in table.c
    assert "lease_expires_at" in table.c

    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_writing_section_plans_node_version" in unique_names
    assert "uq_writing_section_plans_node_fingerprint" not in unique_names

    index_names = {
        index.name
        for index in table.indexes
        if isinstance(index, Index)
    }
    assert "uq_writing_section_plans_current_ready" in index_names
    assert "uq_writing_section_plans_ready_fingerprint" in index_names

    fk_targets = {
        fk.elements[0].column.table.name
        for fk in table.constraints
        if isinstance(fk, ForeignKeyConstraint)
    }
    assert {"projects", "writing_outline_nodes"} <= fk_targets


def test_writing_section_draft_has_nullable_section_plan_link() -> None:
    from app.models.writing_section_draft import WritingSectionDraft

    table = WritingSectionDraft.__table__

    assert table.c.section_plan_id.nullable is True
    assert table.c.planning_mode.nullable is True
    assert table.c.planning_failure_code.nullable is True
    fk_targets = {
        fk.elements[0].column.table.name
        for fk in table.constraints
        if isinstance(fk, ForeignKeyConstraint)
    }
    assert "writing_section_plans" in fk_targets


def test_writing_section_plan_is_registered_for_model_imports() -> None:
    import app.models as models

    assert hasattr(models, "WritingSectionPlan")

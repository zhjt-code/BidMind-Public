"""writing_section_diagrams 模型定义测试（步骤1）。"""

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint


def test_writing_section_diagram_model_declares_expected_columns() -> None:
    from app.models.writing_section_diagram import WritingSectionDiagram

    table = WritingSectionDiagram.__table__

    assert table.name == "writing_section_diagrams"
    for column in (
        "project_id",
        "outline_node_id",
        "section_plan_id",
        "visual_id",
        "diagram_type",
        "title",
        "ir_json",
        "png_path",
        "content_sha256",
        "status",
        "failure_code",
    ):
        assert column in table.c, f"missing column: {column}"

    assert table.c.section_plan_id.nullable is False
    assert table.c.png_path.nullable is True
    assert table.c.failure_code.nullable is True


def test_writing_section_diagram_unique_per_plan_visual() -> None:
    from app.models.writing_section_diagram import WritingSectionDiagram

    table = WritingSectionDiagram.__table__
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_writing_section_diagrams_plan_visual" in unique_names

    fk_targets = {
        fk.elements[0].column.table.name
        for fk in table.constraints
        if isinstance(fk, ForeignKeyConstraint)
    }
    assert {
        "projects",
        "writing_outline_nodes",
        "writing_section_plans",
    } <= fk_targets


def test_writing_section_diagram_is_registered_for_model_imports() -> None:
    import app.models as models

    assert hasattr(models, "WritingSectionDiagram")

"""expand writing_section_diagrams type with construction primitives

Revision ID: c9e1f3a5b7d0
Revises: b8d0f2a4c6e8
Create Date: 2026-08-21 10:20:00.000000+08:00
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c9e1f3a5b7d0"
down_revision: Union[str, None] = "b8d0f2a4c6e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = (
    "diagram_type in ('flowchart', 'gantt', 'swimlane', "
    "'roadmap', 'org_chart', 'layered')"
)
_NEW = (
    "diagram_type in ('flowchart', 'gantt', 'swimlane', "
    "'roadmap', 'org_chart', 'layered', 'network', 'wbs', 'zone_layout')"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_writing_section_diagrams_type",
        "writing_section_diagrams",
        type_="check",
    )
    op.create_check_constraint(
        "ck_writing_section_diagrams_type",
        "writing_section_diagrams",
        _NEW,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_writing_section_diagrams_type",
        "writing_section_diagrams",
        type_="check",
    )
    op.create_check_constraint(
        "ck_writing_section_diagrams_type",
        "writing_section_diagrams",
        _OLD,
    )

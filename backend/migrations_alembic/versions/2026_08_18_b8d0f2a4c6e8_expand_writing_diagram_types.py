"""expand writing_section_diagrams type to six primitives

Revision ID: b8d0f2a4c6e8
Revises: a7c9e1f3b5d7
Create Date: 2026-08-18 09:40:00.000000+08:00
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b8d0f2a4c6e8"
down_revision: Union[str, None] = "a7c9e1f3b5d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "diagram_type in ('flowchart', 'gantt')"
_NEW = (
    "diagram_type in ('flowchart', 'gantt', 'swimlane', "
    "'roadmap', 'org_chart', 'layered')"
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

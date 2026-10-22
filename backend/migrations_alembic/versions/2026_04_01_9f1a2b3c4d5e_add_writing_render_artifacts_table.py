"""add_writing_render_artifacts_table

Revision ID: 9f1a2b3c4d5e
Revises: 23aed414e53b
Create Date: 2026-04-01 11:30:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f1a2b3c4d5e"
down_revision: Union[str, None] = "23aed414e53b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "writing_render_artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "render_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="当前渲染版本号，每次显式重新渲染递增",
        ),
        sa.Column(
            "preview_html",
            sa.Text(),
            nullable=True,
            comment="当前渲染版本对应的HTML预览快照",
        ),
        sa.Column(
            "docx_blob",
            sa.LargeBinary(),
            nullable=True,
            comment="当前渲染版本对应的DOCX字节流",
        ),
        sa.Column(
            "docx_size",
            sa.Integer(),
            nullable=True,
            comment="DOCX字节长度，用于诊断",
        ),
        sa.Column(
            "rendered_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="当前渲染版本生成时间",
        ),
        sa.Column(
            "consistency_issues_json",
            sa.Text(),
            nullable=True,
            comment="一致性检查结果(JSON字符串)",
        ),
        sa.Column(
            "consistency_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="当前渲染版本的一致性检查完成时间",
        ),
        sa.Column(
            "consistency_render_version",
            sa.Integer(),
            nullable=True,
            comment="一致性结果对应的render_version",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index(
        "ix_writing_render_artifacts_id",
        "writing_render_artifacts",
        ["id"],
    )
    op.create_index(
        "ix_writing_render_artifacts_project_id",
        "writing_render_artifacts",
        ["project_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_writing_render_artifacts_project_id",
        table_name="writing_render_artifacts",
    )
    op.drop_index(
        "ix_writing_render_artifacts_id",
        table_name="writing_render_artifacts",
    )
    op.drop_table("writing_render_artifacts")

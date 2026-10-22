"""add_metric_catalog_snapshot

Phase 3 监控体系: 新增 metric_catalog (启动期扫描自动同步的指标目录) 与
metric_snapshots (时序温层, 90d/1h 聚合, 供 Phase 4 趋势判断读取).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-23 12:00:00.000000+08:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b34mc5d6e7f8"
down_revision: Union[str, None] = "a23ob1c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metric_catalog",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("layer", sa.String(length=32), nullable=False),
        sa.Column("pillar", sa.String(length=32), nullable=False),
        sa.Column("business", sa.String(length=32), nullable=False, server_default="all"),
        sa.Column("direction", sa.String(length=32), nullable=False, server_default="neutral"),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="counter"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("labels_json", sa.Text(), nullable=True),
        sa.Column("thresholds_json", sa.Text(), nullable=True),
        sa.Column("source_module", sa.String(length=256), nullable=True),
        sa.Column("source_qualname", sa.String(length=256), nullable=True),
        sa.Column("source_file", sa.String(length=512), nullable=True),
        sa.Column("source_line", sa.Integer(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_metric_catalog_name"),
    )
    op.create_index("ix_metric_catalog_name", "metric_catalog", ["name"])
    op.create_index("ix_metric_catalog_layer", "metric_catalog", ["layer"])
    op.create_index("ix_metric_catalog_pillar", "metric_catalog", ["pillar"])
    op.create_index("ix_metric_catalog_business", "metric_catalog", ["business"])

    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("labels_hash", sa.String(length=32), nullable=False),
        sa.Column("bucket_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg", sa.Float(), nullable=False, server_default="0"),
        sa.Column("p50", sa.Float(), nullable=True),
        sa.Column("p95", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_metric_snapshots_name", "metric_snapshots", ["name"])
    op.create_index("ix_metric_snapshots_labels_hash", "metric_snapshots", ["labels_hash"])
    op.create_index("ix_metric_snapshots_bucket_ts", "metric_snapshots", ["bucket_ts"])
    op.create_index(
        "ix_metric_snapshots_name_bucket",
        "metric_snapshots",
        ["name", "bucket_ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_metric_snapshots_name_bucket", table_name="metric_snapshots")
    op.drop_index("ix_metric_snapshots_bucket_ts", table_name="metric_snapshots")
    op.drop_index("ix_metric_snapshots_labels_hash", table_name="metric_snapshots")
    op.drop_index("ix_metric_snapshots_name", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")

    op.drop_index("ix_metric_catalog_business", table_name="metric_catalog")
    op.drop_index("ix_metric_catalog_pillar", table_name="metric_catalog")
    op.drop_index("ix_metric_catalog_layer", table_name="metric_catalog")
    op.drop_index("ix_metric_catalog_name", table_name="metric_catalog")
    op.drop_table("metric_catalog")

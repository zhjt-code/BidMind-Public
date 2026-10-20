"""add_llm_center_tables

Revision ID: d2e3f4a5b6c7
Revises: c4d5e6f7a8b9
Create Date: 2026-04-10

LLM Center 编排管理系统数据模型:
- llm_flows: 流程定义
- llm_stages: 阶段定义
- llm_nodes: 节点定义
- llm_template_versions: Prompt 模板版本
- llm_param_defs: 参数定义
- llm_node_edges: 节点连接
- llm_publish_records: 发布记录
- llm_execution_traces: 执行轨迹
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. llm_flows ──
    op.create_table(
        "llm_flows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("flow_key", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("route_prefix", sa.String(50), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("flow_key", name="uq_llm_flows_flow_key"),
    )
    op.create_index(op.f("ix_llm_flows_id"), "llm_flows", ["id"])

    # ── 2. llm_stages ──
    op.create_table(
        "llm_stages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("flow_id", sa.Integer(), sa.ForeignKey("llm_flows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_key", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("flow_id", "stage_key", name="uq_llm_stage_flow_key"),
    )
    op.create_index(op.f("ix_llm_stages_id"), "llm_stages", ["id"])
    op.create_index("ix_llm_stages_flow_id", "llm_stages", ["flow_id"])

    # ── 3. llm_nodes ──
    op.create_table(
        "llm_nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stage_id", sa.Integer(), sa.ForeignKey("llm_stages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("builder_key", sa.String(200), nullable=True),
        sa.Column("resolver_key", sa.String(200), nullable=True),
        sa.Column("source_file", sa.String(200), nullable=True),
        sa.Column("execution_channel", sa.String(20), nullable=False, server_default="review"),
        sa.Column("call_method", sa.String(30), nullable=False, server_default="chat_json"),
        sa.Column("default_temperature", sa.Float(), nullable=False, server_default="0.1"),
        sa.Column("default_max_tokens", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column("output_schema_name", sa.String(100), nullable=True),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="'main'"),
        sa.Column("editable_level", sa.String(20), nullable=False, server_default="'advanced'"),
        sa.Column("supports_hot_reload", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_key", name="uq_llm_nodes_node_key"),
    )
    op.create_index(op.f("ix_llm_nodes_id"), "llm_nodes", ["id"])
    op.create_index("ix_llm_nodes_stage_id", "llm_nodes", ["stage_id"])

    # ── 4. llm_template_versions ──
    op.create_table(
        "llm_template_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_role", sa.String(10), nullable=False, server_default="'system'"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source", sa.String(10), nullable=False, server_default="'code'"),
        sa.Column("status", sa.String(20), nullable=False, server_default="'draft'"),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "template_role", "version", name="uq_llm_tv_node_role_version"),
    )
    op.create_index(op.f("ix_llm_template_versions_id"), "llm_template_versions", ["id"])
    op.create_index("ix_llm_tv_node_id", "llm_template_versions", ["node_id"])
    op.create_index("ix_llm_tv_is_active", "llm_template_versions", ["is_active"])

    # ── 5. llm_param_defs ──
    op.create_table(
        "llm_param_defs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("param_key", sa.String(80), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("data_type", sa.String(30), nullable=False, server_default="'string'"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("default_value", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=False, server_default="'manual'"),
        sa.Column("source_hint", sa.String(200), nullable=True),
        sa.Column("example_value", sa.Text(), nullable=True),
        sa.Column("ui_hint", sa.String(20), nullable=False, server_default="'tooltip'"),
        sa.Column("status", sa.String(20), nullable=False, server_default="'active'"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "direction", "param_key", name="uq_llm_pd_node_dir_key"),
    )
    op.create_index(op.f("ix_llm_param_defs_id"), "llm_param_defs", ["id"])
    op.create_index("ix_llm_pd_node_id", "llm_param_defs", ["node_id"])

    # ── 6. llm_node_edges ──
    op.create_table(
        "llm_node_edges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("from_node_id", sa.Integer(), sa.ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_node_id", sa.Integer(), sa.ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("edge_type", sa.String(20), nullable=False, server_default="'data_flow'"),
        sa.Column("param_mapping", postgresql.JSONB(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_node_id", "to_node_id", name="uq_llm_edge_from_to"),
    )
    op.create_index(op.f("ix_llm_node_edges_id"), "llm_node_edges", ["id"])
    op.create_index("ix_llm_edge_from", "llm_node_edges", ["from_node_id"])
    op.create_index("ix_llm_edge_to", "llm_node_edges", ["to_node_id"])

    # ── 7. llm_publish_records ──
    op.create_table(
        "llm_publish_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("llm_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_version_id", sa.Integer(), sa.ForeignKey("llm_template_versions.id"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("published_by", sa.String(100), nullable=False),
        sa.Column("publish_scope", sa.String(50), nullable=True),
        sa.Column("previous_version_id", sa.Integer(), sa.ForeignKey("llm_template_versions.id"), nullable=True),
        sa.Column("validation_result", postgresql.JSONB(), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_publish_records_id"), "llm_publish_records", ["id"])
    op.create_index("ix_llm_pr_node_id", "llm_publish_records", ["node_id"])

    # ── 8. llm_execution_traces ──
    op.create_table(
        "llm_execution_traces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("node_key", sa.String(80), nullable=False),
        sa.Column("template_version_id", sa.Integer(), nullable=True),
        sa.Column("execution_channel", sa.String(20), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("trace_mode", sa.String(10), nullable=False, server_default="'aggregate'"),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("total_latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("fallback_info", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_execution_traces_id"), "llm_execution_traces", ["id"])
    op.create_index("idx_exec_traces_node_project", "llm_execution_traces", ["node_key", "project_id"])
    op.create_index("idx_exec_traces_created", "llm_execution_traces", ["created_at"])


def downgrade() -> None:
    op.drop_table("llm_execution_traces")
    op.drop_table("llm_publish_records")
    op.drop_table("llm_node_edges")
    op.drop_table("llm_param_defs")
    op.drop_table("llm_template_versions")
    op.drop_table("llm_nodes")
    op.drop_table("llm_stages")
    op.drop_table("llm_flows")

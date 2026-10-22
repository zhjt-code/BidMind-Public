"""split_subcontractors_from_metadata_info: 从 collusion_extractions.metadata_info JSON 子字典中拆出分包商字段
到独立列 subcontractors_info。metadata_info 应当仅存 PDF 元数据（D8 维度），
分包商是 LLM 输出的业务字段，语义错位。

Upgrade:
 1. 新增列 collusion_extractions.subcontractors_info JSON
 2. 把存量 metadata_info->'subcontractors' 拷到新列
 3. 从 metadata_info 中移除 subcontractors 子键

Downgrade:
 1. 把 subcontractors_info 写回 metadata_info.subcontractors
 2. 删除 subcontractors_info 列

Revision ID: b2f7d8e9a0c1
Revises: d5a6b7c8e9f0
Create Date: 2026-04-25 10:00:00.000000+08:00

Note: 前置迁移 add_bid_response_chunks 因 revision 撞车已改为
d5a6b7c8e9f0，本文件同步指向新 id；历史上一度指向 f6a7b8c9d0e1。
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b2f7d8e9a0c1"
down_revision: Union[str, None] = "d5a6b7c8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 新增独立 JSON 列承载 subcontractors（LLM 业务字段）。
    # metadata_info 是 JSON 不是 JSONB；全迁移保持 JSON 语义，避免历史 Unicode
    # escape 在 ::jsonb 转换时触发 UntranslatableCharacter。
    op.execute(
        "ALTER TABLE collusion_extractions "
        "ADD COLUMN IF NOT EXISTS subcontractors_info JSON"
    )
    op.execute(
        "ALTER TABLE collusion_extractions "
        "ALTER COLUMN subcontractors_info TYPE JSON USING subcontractors_info::json"
    )
    op.execute(
        "COMMENT ON COLUMN collusion_extractions.subcontractors_info "
        "IS '分包商/供应商列表（LLM业务字段）'"
    )

    # 2. 把存量 metadata_info.subcontractors 拷到新列（仅当新列为空时）。
    # 历史 JSON 字符串里可能包含 \u0000；PostgreSQL JSON 函数读取该值时会
    # 报 UntranslatableCharacter，因此先在 JSON 文本层面移除该 escape 再解析。
    op.execute(
        """
        WITH normalized AS (
            SELECT
                id,
                replace(metadata_info::text, '\\u0000', '')::json AS metadata_info_clean
            FROM collusion_extractions
            WHERE metadata_info IS NOT NULL
        )
        UPDATE collusion_extractions AS ce
        SET subcontractors_info = normalized.metadata_info_clean -> 'subcontractors'
        FROM normalized
        WHERE ce.id = normalized.id
          AND json_typeof(normalized.metadata_info_clean) = 'object'
          AND normalized.metadata_info_clean -> 'subcontractors' IS NOT NULL
          AND ce.subcontractors_info IS NULL
        """
    )

    # 3. 从 metadata_info 中移除 subcontractors 子键，修复语义错位。
    # json_each/json_object_agg 保留 JSON 列类型，并复用清洗后的 JSON 值。
    op.execute(
        """
        WITH normalized AS (
            SELECT
                id,
                replace(metadata_info::text, '\\u0000', '')::json AS metadata_info_clean
            FROM collusion_extractions
            WHERE metadata_info IS NOT NULL
        )
        UPDATE collusion_extractions AS ce
        SET metadata_info = COALESCE(
            (
                SELECT json_object_agg(item.key, item.value)
                FROM json_each(normalized.metadata_info_clean) AS item(key, value)
                WHERE item.key <> 'subcontractors'
            ),
            '{}'::json
        )
        FROM normalized
        WHERE ce.id = normalized.id
          AND json_typeof(normalized.metadata_info_clean) = 'object'
          AND normalized.metadata_info_clean -> 'subcontractors' IS NOT NULL
        """
    )


def downgrade() -> None:
    # 1. 把 subcontractors_info 回填到 metadata_info.subcontractors，
    #    以便旧版本代码（从 metadata_info 倒推 subcontractors）仍能读到。
    op.execute(
        """
        UPDATE collusion_extractions AS ce
        SET metadata_info = (
            SELECT json_object_agg(merged.key, merged.value)
            FROM (
                SELECT item.key, item.value
                FROM json_each(COALESCE(ce.metadata_info, '{}'::json)) AS item(key, value)
                WHERE item.key <> 'subcontractors'
                UNION ALL
                SELECT 'subcontractors', ce.subcontractors_info
            ) AS merged(key, value)
        )
        WHERE ce.subcontractors_info IS NOT NULL
        """
    )

    # 2. 删除独立列
    op.execute(
        "ALTER TABLE collusion_extractions DROP COLUMN IF EXISTS subcontractors_info"
    )



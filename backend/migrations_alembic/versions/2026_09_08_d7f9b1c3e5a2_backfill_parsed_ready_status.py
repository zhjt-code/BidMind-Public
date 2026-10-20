"""backfill parsed ready status after required documents completed

Revision ID: d7f9b1c3e5a2
Revises: c6e8a0b2d4f1
Create Date: 2026-09-08

文档已齐套但仍停在 UPLOADING/PARSING 的项目，回填为 PARSED。
判定与 document_parse_readiness.required_documents_are_parsed 对齐。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d7f9b1c3e5a2"
down_revision: Union[str, None] = "c6e8a0b2d4f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE projects p
        SET status = 'PARSED'
        WHERE p.status IN ('UPLOADING', 'PARSING')
          AND p.project_type = 'BID_WRITING'
          AND EXISTS (
              SELECT 1 FROM documents d
              WHERE d.project_id = p.id
                AND d.doc_type = 'TENDER'
                AND d.parsed = 1
          )
          AND EXISTS (SELECT 1 FROM documents d WHERE d.project_id = p.id)
          AND NOT EXISTS (
              SELECT 1 FROM documents d
              WHERE d.project_id = p.id AND d.parsed <> 1
          )
        """
    )
    op.execute(
        """
        UPDATE projects p
        SET status = 'PARSED'
        WHERE p.status IN ('UPLOADING', 'PARSING')
          AND p.project_type = 'BID_REVIEW'
          AND EXISTS (
              SELECT 1 FROM documents d
              WHERE d.project_id = p.id
                AND d.doc_type = 'TENDER'
                AND d.parsed = 1
          )
          AND EXISTS (
              SELECT 1 FROM documents d
              WHERE d.project_id = p.id
                AND d.doc_type = 'BID'
                AND d.parsed = 1
          )
          AND NOT EXISTS (
              SELECT 1 FROM documents d
              WHERE d.project_id = p.id AND d.parsed <> 1
          )
        """
    )
    op.execute(
        """
        UPDATE projects p
        SET status = 'PARSED'
        WHERE p.status IN ('UPLOADING', 'PARSING')
          AND p.project_type = 'COLLUSION_CHECK'
          AND (
              SELECT COUNT(*) FROM documents d
              WHERE d.project_id = p.id
                AND d.doc_type = 'BID'
                AND d.parsed = 1
          ) >= 2
          AND NOT EXISTS (
              SELECT 1 FROM documents d
              WHERE d.project_id = p.id AND d.parsed <> 1
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE projects
        SET status = 'UPLOADING'
        WHERE status = 'PARSED'
        """
    )

"""consolidate_schema_gaps

Revision ID: a0b1c2d3e4f5
Revises: f4a5b6c7d8e9
Create Date: 2026-04-13

Bring the Alembic migration chain in sync with the actual schema that was
previously managed by create_all + _apply_schema_migrations().  After this
migration Alembic becomes the single source of truth for all DDL changes.

Gap categories addressed:
  1. collusion_groups table (created by create_all, never in Alembic chain)
  2. Missing columns on existing tables (added by _apply_schema_migrations)
  3. Column type corrections (login_attempts String->Integer, analysis_mode width)
  4. Duplicate idx_* indexes (created by _apply, shadowing ix_* from ORM)
  5. Stale column removal (documents.requirements_extracted)
  6. Missing unique constraint (review_results uq_review_project_requirement)

All operations use IF [NOT] EXISTS to be idempotent on databases where
create_all + _apply_schema_migrations already ran.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_column_if_not_exists(table: str, column: str, definition: str) -> None:
    """PostgreSQL 9.6+ supports ADD COLUMN IF NOT EXISTS."""
    op.execute(text(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"
    ))


def _drop_index_if_exists(name: str) -> None:
    op.execute(text(f"DROP INDEX IF EXISTS {name}"))


def _create_index_if_not_exists(name: str, table: str, columns: str,
                                unique: bool = False) -> None:
    uq = "UNIQUE " if unique else ""
    op.execute(text(
        f"CREATE {uq}INDEX IF NOT EXISTS {name} ON {table} ({columns})"
    ))


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. CREATE collusion_groups table ─────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS collusion_groups (
            id              SERIAL PRIMARY KEY,
            created_at      TIMESTAMPTZ DEFAULT now(),
            updated_at      TIMESTAMPTZ DEFAULT now(),
            project_id      INTEGER NOT NULL
                            REFERENCES projects(id) ON DELETE CASCADE,
            group_index     INTEGER NOT NULL,
            member_doc_ids  JSON    NOT NULL,
            member_names    JSON    NOT NULL,
            risk_level      VARCHAR(20) NOT NULL DEFAULT 'medium',
            aggregated_evidence  JSON,
            role_analysis        JSON,
            network_description  TEXT,
            collusion_strategy   TEXT,
            investigation_focus  JSON,
            related_pair_ids     JSON,
            llm_group_analysis   JSON,
            group_findings       JSON,
            CONSTRAINT uq_cg_project_group UNIQUE (project_id, group_index)
        )
    """))
    _create_index_if_not_exists(
        "ix_collusion_groups_project_id", "collusion_groups", "project_id")

    # ── 2. ADD missing columns ───────────────────────────────────────
    _add_column_if_not_exists(
        "chunks", "chunk_type",
        "VARCHAR(32) NOT NULL DEFAULT 'text'")

    _add_column_if_not_exists(
        "projects", "collusion_summary", "JSON")

    _add_column_if_not_exists(
        "requirements", "source_pages", "TEXT")
    _add_column_if_not_exists(
        "requirements", "keywords", "TEXT")
    _add_column_if_not_exists(
        "requirements", "sub_requirements", "TEXT")
    _add_column_if_not_exists(
        "requirements", "merged_count", "INTEGER")

    # ── 3. ALTER column types ────────────────────────────────────────
    # users.login_attempts: baseline had String(10), ORM is Integer
    op.execute(text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users'
                  AND column_name = 'login_attempts'
                  AND data_type = 'character varying'
            ) THEN
                ALTER TABLE users
                    ALTER COLUMN login_attempts
                    TYPE INTEGER USING login_attempts::integer;
            END IF;
        END $$
    """))

    # collusion_results.analysis_mode: baseline had VARCHAR(20), ORM is VARCHAR(50)
    op.execute(text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'collusion_results'
                  AND column_name = 'analysis_mode'
                  AND character_maximum_length IS NOT NULL
                  AND character_maximum_length < 50
            ) THEN
                ALTER TABLE collusion_results
                    ALTER COLUMN analysis_mode TYPE VARCHAR(50);
            END IF;
        END $$
    """))

    # ── 4. DROP duplicate idx_* indexes ──────────────────────────────
    # These were created by _apply_schema_migrations() and shadow the
    # ix_* indexes that the ORM + Alembic baseline define.

    # requirements
    _drop_index_if_exists("idx_requirements_canonical_key")
    _drop_index_if_exists("idx_requirements_main_requirement_id")

    # review_results
    _drop_index_if_exists("idx_review_results_canonical_key")
    _drop_index_if_exists("idx_review_results_fact_key")
    _drop_index_if_exists("idx_review_results_family_type")
    _drop_index_if_exists("idx_review_results_main_requirement_id")

    # writing_materials
    _drop_index_if_exists("idx_writing_materials_parent_material_id")
    _drop_index_if_exists("idx_writing_materials_role_status")

    # collusion_extractions (from _apply or legacy)
    _drop_index_if_exists("idx_collusion_extractions_project_doc")
    _drop_index_if_exists("idx_collusion_extractions_project_id")

    # collusion_results (from _apply or legacy)
    _drop_index_if_exists("idx_collusion_results_doc_pair")
    _drop_index_if_exists("idx_collusion_results_project_id")

    # review_fact tables (from legacy creation, not ORM naming)
    _drop_index_if_exists("idx_review_facts_fact_uid")
    _drop_index_if_exists("idx_review_facts_project_id")
    _drop_index_if_exists("idx_review_fact_links_fact_id")
    _drop_index_if_exists("idx_review_fact_links_requirement_id")
    _drop_index_if_exists("idx_review_fact_links_bid_response_id")
    _drop_index_if_exists("idx_review_fact_judgements_fact_id")
    _drop_index_if_exists("idx_review_fact_judgements_requirement_id")
    _drop_index_if_exists("idx_review_fact_judgements_review_result_id")

    # ── 5. Ensure ORM-declared ix_* indexes exist ────────────────────
    # On databases that were stamped (not migrated through baseline),
    # these indexes may not exist yet.

    # requirements
    _create_index_if_not_exists(
        "ix_requirements_canonical_key", "requirements", "canonical_key")
    _create_index_if_not_exists(
        "ix_requirements_main_requirement_id", "requirements",
        "main_requirement_id")

    # review_results
    _create_index_if_not_exists(
        "ix_review_results_canonical_key", "review_results", "canonical_key")
    _create_index_if_not_exists(
        "ix_review_results_fact_key", "review_results", "fact_key")
    _create_index_if_not_exists(
        "ix_review_results_family_type", "review_results", "family_type")
    _create_index_if_not_exists(
        "ix_review_results_main_requirement_id", "review_results",
        "main_requirement_id")

    # writing_materials
    _create_index_if_not_exists(
        "ix_writing_materials_parent_material_id", "writing_materials",
        "parent_material_id")

    # collusion_extractions
    _create_index_if_not_exists(
        "ix_collusion_extractions_project_id", "collusion_extractions",
        "project_id")

    # collusion_results
    _create_index_if_not_exists(
        "ix_collusion_results_project_id", "collusion_results", "project_id")

    # review_facts / review_fact_links / review_fact_judgements
    _create_index_if_not_exists(
        "ix_review_facts_fact_uid", "review_facts", "fact_uid")
    _create_index_if_not_exists(
        "ix_review_facts_project_id", "review_facts", "project_id")
    _create_index_if_not_exists(
        "ix_review_fact_links_fact_id", "review_fact_links", "fact_id")
    _create_index_if_not_exists(
        "ix_review_fact_links_requirement_id", "review_fact_links",
        "requirement_id")
    _create_index_if_not_exists(
        "ix_review_fact_links_bid_response_id", "review_fact_links",
        "bid_response_id")
    _create_index_if_not_exists(
        "ix_review_fact_judgements_fact_id", "review_fact_judgements",
        "fact_id")
    _create_index_if_not_exists(
        "ix_review_fact_judgements_requirement_id",
        "review_fact_judgements", "requirement_id")
    _create_index_if_not_exists(
        "ix_review_fact_judgements_review_result_id",
        "review_fact_judgements", "review_result_id")

    # ── 6. Missing unique constraint on review_results ───────────────
    # Deduplicate first: keep only the row with the highest id for each
    # (project_id, requirement_id) pair, then add the constraint.
    op.execute(text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_review_project_requirement'
            ) THEN
                DELETE FROM review_results a
                USING review_results b
                WHERE a.project_id = b.project_id
                  AND a.requirement_id = b.requirement_id
                  AND a.id < b.id;

                ALTER TABLE review_results
                    ADD CONSTRAINT uq_review_project_requirement
                    UNIQUE (project_id, requirement_id);
            END IF;
        END $$
    """))

    # ── 7. DROP stale column ─────────────────────────────────────────
    op.execute(text(
        "ALTER TABLE documents "
        "DROP COLUMN IF EXISTS requirements_extracted"
    ))

    # ── 8. Ensure id indexes on collusion tables ────────────────────
    # BaseModel declares id with index=True; collusion tables created by
    # create_all already have the PK index, but the ORM also wants
    # a separate ix_<table>_id index.
    _create_index_if_not_exists(
        "ix_collusion_extractions_id", "collusion_extractions", "id")
    _create_index_if_not_exists(
        "ix_collusion_results_id", "collusion_results", "id")
    _create_index_if_not_exists(
        "ix_collusion_groups_id", "collusion_groups", "id")

    # ── 9. Convert unique indexes to proper constraints ──────────────
    # _apply_schema_migrations created uq_ce_project_doc / uq_cr_pair as
    # CREATE UNIQUE INDEX, but the ORM declares UniqueConstraint.
    # PG stores them differently; convert to constraints so autogenerate
    # sees them correctly.
    for idx_name, table, columns in [
        ("uq_ce_project_doc", "collusion_extractions",
         "project_id, document_id"),
        ("uq_cr_pair", "collusion_results",
         "project_id, doc_a_id, doc_b_id"),
    ]:
        op.execute(text(f"""
            DO $$ BEGIN
                -- Drop the standalone unique INDEX if it exists but is not
                -- backing a constraint (contype = 'u').
                IF EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE indexname = '{idx_name}'
                ) AND NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{idx_name}'
                ) THEN
                    DROP INDEX {idx_name};
                    ALTER TABLE {table}
                        ADD CONSTRAINT {idx_name} UNIQUE ({columns});
                END IF;
            END $$
        """))


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    # 7. Restore stale column
    _add_column_if_not_exists(
        "documents", "requirements_extracted",
        "INTEGER NOT NULL DEFAULT 0")

    # 6. Drop constraint
    op.execute(text("""
        ALTER TABLE review_results
        DROP CONSTRAINT IF EXISTS uq_review_project_requirement
    """))

    # 5 & 4. Index changes are symmetric -- skip detailed reversal
    # as this migration is meant to be a one-way consolidation.

    # 3. Column type reversals (unsafe for production, skip)

    # 2. Column additions are not reversed (data-loss risk)

    # 1. collusion_groups table is not dropped (data-loss risk)
    pass

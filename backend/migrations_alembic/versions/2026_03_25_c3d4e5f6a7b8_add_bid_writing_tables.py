"""add bid_writing tables and enum extensions

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-25

Phase W1: 标书编写基础设施
- 扩展 projecttype / projectstatus / documenttype 枚举
- 创建 writing_clauses / writing_outline_nodes / writing_materials
        / writing_bindings / writing_section_drafts / writing_render_profiles 六张表
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. 扩展现有枚举类型                                                  #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TYPE projecttype ADD VALUE IF NOT EXISTS 'BID_WRITING'")
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'OUTLINING'")
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'MATERIAL_BINDING'")
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'WRITING'")
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'FORMATTING'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'MATERIAL'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'REFERENCE_BID'")

    # ------------------------------------------------------------------ #
    # 2. 创建新枚举类型                                                    #
    # ------------------------------------------------------------------ #
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE writingclauselayer AS ENUM (
                'hard_constraint', 'structure_constraint', 'generation_guide'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE outlinenodetype AS ENUM (
                'fixed_template', 'data_fill', 'free_writing'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE outlinenodestatus AS ENUM (
                'pending', 'generating', 'draft', 'user_edited', 'finalized'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE materialtype AS ENUM (
                'text', 'image', 'table', 'certificate', 'personnel',
                'performance', 'old_bid_section', 'template_fragment'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE bindingtype AS ENUM (
                'clause_to_node', 'material_to_node', 'material_to_clause'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE bindingstatus AS ENUM (
                'suggested', 'confirmed', 'rejected'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE draftstatus AS ENUM (
                'generating', 'draft', 'reviewed', 'finalized'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)

    # ------------------------------------------------------------------ #
    # 3. writing_clauses                                                  #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE writing_clauses (
            id          SERIAL PRIMARY KEY,
            project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            layer       writingclauselayer NOT NULL,
            sub_type    VARCHAR(50) NOT NULL,
            content     TEXT NOT NULL,
            raw_text    TEXT,
            source_page INTEGER,
            score_weight DOUBLE PRECISION,
            confirmed   INTEGER NOT NULL DEFAULT 0,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_writing_clauses_id ON writing_clauses (id)")
    op.execute("CREATE INDEX ix_writing_clauses_project_id ON writing_clauses (project_id)")

    # ------------------------------------------------------------------ #
    # 4. writing_outline_nodes (自引用外键)                                #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE writing_outline_nodes (
            id               SERIAL PRIMARY KEY,
            project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            parent_id        INTEGER REFERENCES writing_outline_nodes(id) ON DELETE CASCADE,
            title            VARCHAR(500) NOT NULL,
            depth            INTEGER NOT NULL DEFAULT 0,
            sort_order       INTEGER NOT NULL DEFAULT 0,
            node_type        outlinenodetype NOT NULL,
            is_required      INTEGER NOT NULL DEFAULT 1,
            score_weight     DOUBLE PRECISION,
            target_word_count INTEGER,
            status           outlinenodestatus NOT NULL DEFAULT 'pending',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_writing_outline_nodes_id ON writing_outline_nodes (id)")
    op.execute("CREATE INDEX ix_writing_outline_nodes_project_id ON writing_outline_nodes (project_id)")

    # ------------------------------------------------------------------ #
    # 5. writing_materials                                                #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE writing_materials (
            id            SERIAL PRIMARY KEY,
            project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            document_id   INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            material_type materialtype NOT NULL,
            title         VARCHAR(500) NOT NULL,
            content       TEXT,
            file_path     VARCHAR(500),
            metadata_tags TEXT,
            source_page   INTEGER,
            chunk_ids     TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_writing_materials_id ON writing_materials (id)")
    op.execute("CREATE INDEX ix_writing_materials_project_id ON writing_materials (project_id)")

    # ------------------------------------------------------------------ #
    # 6. writing_bindings                                                 #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE writing_bindings (
            id              SERIAL PRIMARY KEY,
            project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            outline_node_id INTEGER REFERENCES writing_outline_nodes(id) ON DELETE CASCADE,
            clause_id       INTEGER REFERENCES writing_clauses(id) ON DELETE CASCADE,
            material_id     INTEGER REFERENCES writing_materials(id) ON DELETE CASCADE,
            binding_type    bindingtype NOT NULL,
            confidence      DOUBLE PRECISION NOT NULL DEFAULT 0,
            source          VARCHAR(10) NOT NULL DEFAULT 'auto',
            status          bindingstatus NOT NULL DEFAULT 'suggested',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_writing_bindings_id ON writing_bindings (id)")
    op.execute("CREATE INDEX ix_writing_bindings_project_id ON writing_bindings (project_id)")

    # ------------------------------------------------------------------ #
    # 7. writing_section_drafts                                           #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE writing_section_drafts (
            id                  SERIAL PRIMARY KEY,
            outline_node_id     INTEGER NOT NULL REFERENCES writing_outline_nodes(id) ON DELETE CASCADE,
            version             INTEGER NOT NULL DEFAULT 1,
            content             TEXT,
            content_format      VARCHAR(20) NOT NULL DEFAULT 'html',
            summary             VARCHAR(1000),
            source_clause_ids   TEXT,
            source_material_ids TEXT,
            is_current          INTEGER NOT NULL DEFAULT 1,
            is_user_edited      INTEGER NOT NULL DEFAULT 0,
            is_locked           INTEGER NOT NULL DEFAULT 0,
            word_count          INTEGER,
            status              draftstatus NOT NULL DEFAULT 'generating',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_writing_section_drafts_id ON writing_section_drafts (id)")
    op.execute("CREATE INDEX ix_writing_section_drafts_outline_node_id ON writing_section_drafts (outline_node_id)")

    # ------------------------------------------------------------------ #
    # 8. writing_render_profiles                                          #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE writing_render_profiles (
            id                   SERIAL PRIMARY KEY,
            project_id           INTEGER NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
            font_body            VARCHAR(100) NOT NULL DEFAULT '仿宋',
            font_heading         VARCHAR(100) NOT NULL DEFAULT '黑体',
            font_size_body       DOUBLE PRECISION NOT NULL DEFAULT 12.0,
            font_size_h1         DOUBLE PRECISION NOT NULL DEFAULT 22.0,
            font_size_h2         DOUBLE PRECISION NOT NULL DEFAULT 16.0,
            font_size_h3         DOUBLE PRECISION NOT NULL DEFAULT 14.0,
            line_spacing         DOUBLE PRECISION NOT NULL DEFAULT 1.5,
            margin_top           DOUBLE PRECISION NOT NULL DEFAULT 25.4,
            margin_bottom        DOUBLE PRECISION NOT NULL DEFAULT 25.4,
            margin_left          DOUBLE PRECISION NOT NULL DEFAULT 31.8,
            margin_right         DOUBLE PRECISION NOT NULL DEFAULT 31.8,
            header_text          VARCHAR(200),
            footer_text          VARCHAR(200),
            page_number_position VARCHAR(20) NOT NULL DEFAULT 'bottom_center',
            cover_template_path  VARCHAR(500),
            source_clause_ids    TEXT,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_writing_render_profiles_id ON writing_render_profiles (id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS writing_render_profiles CASCADE")
    op.execute("DROP TABLE IF EXISTS writing_section_drafts CASCADE")
    op.execute("DROP TABLE IF EXISTS writing_bindings CASCADE")
    op.execute("DROP TABLE IF EXISTS writing_materials CASCADE")
    op.execute("DROP TABLE IF EXISTS writing_outline_nodes CASCADE")
    op.execute("DROP TABLE IF EXISTS writing_clauses CASCADE")
    op.execute("DROP TYPE IF EXISTS draftstatus CASCADE")
    op.execute("DROP TYPE IF EXISTS bindingstatus CASCADE")
    op.execute("DROP TYPE IF EXISTS bindingtype CASCADE")
    op.execute("DROP TYPE IF EXISTS materialtype CASCADE")
    op.execute("DROP TYPE IF EXISTS outlinenodestatus CASCADE")
    op.execute("DROP TYPE IF EXISTS outlinenodetype CASCADE")
    op.execute("DROP TYPE IF EXISTS writingclauselayer CASCADE")

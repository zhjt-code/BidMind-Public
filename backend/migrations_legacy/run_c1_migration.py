"""C1.5 数据库迁移脚本 - 运行方式: python migrations/run_c1_migration.py"""

import asyncio
import os
import sys

# 确保工作目录正确
script_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(script_dir)
sys.path.insert(0, backend_dir)
os.chdir(backend_dir)

from dotenv import load_dotenv
load_dotenv()

import asyncpg
from urllib.parse import urlparse


def get_asyncpg_dsn() -> str:
    """将 SQLAlchemy 的 DATABASE_URL 转换为 asyncpg DSN"""
    db_url = os.getenv("DATABASE_URL", "")
    # 替换 asyncpg 前缀
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    return db_url


STATEMENTS = [
    # 1. 创建 projecttype 枚举（幂等）
    """
    DO $outer$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'projecttype') THEN
            CREATE TYPE projecttype AS ENUM ('BID_REVIEW', 'COLLUSION_CHECK');
            RAISE NOTICE 'Created enum: projecttype';
        ELSE
            RAISE NOTICE 'Enum projecttype already exists, skipping';
        END IF;
    END
    $outer$
    """,

    # 2. projects 表新增 project_type 列（幂等）
    """
    DO $outer$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'projects' AND column_name = 'project_type'
        ) THEN
            ALTER TABLE projects ADD COLUMN project_type projecttype NOT NULL DEFAULT 'BID_REVIEW';
            RAISE NOTICE 'Added column: projects.project_type';
        ELSE
            RAISE NOTICE 'Column projects.project_type already exists, skipping';
        END IF;
    END
    $outer$
    """,

    # 3. 创建 collusion_results 表（幂等）
    """
    CREATE TABLE IF NOT EXISTS collusion_results (
        id                       SERIAL PRIMARY KEY,
        created_at               TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at               TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        project_id               INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        doc_a_id                 INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        doc_b_id                 INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        overall_risk             VARCHAR(20) NOT NULL DEFAULT 'inconclusive',
        similarity_score         FLOAT,
        d1_personnel_overlap     FLOAT,
        d2_company_relation      FLOAT,
        d3_text_similarity       FLOAT,
        d4_price_pattern         FLOAT,
        d5_qualification_overlap FLOAT,
        d6_format_similarity     FLOAT,
        d7_abnormal_consistency  FLOAT,
        d8_metadata_similarity   FLOAT,
        findings                 JSONB,
        evidence_chunks          JSONB,
        llm_analysis             JSONB,
        analysis_mode            VARCHAR(20) NOT NULL DEFAULT 'algorithm_only'
    )
    """,

    # 4. collusion_results 索引（幂等）
    "CREATE INDEX IF NOT EXISTS idx_collusion_results_project_id ON collusion_results(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_collusion_results_doc_pair ON collusion_results(doc_a_id, doc_b_id)",

    # 5. 创建 collusion_extractions 表（幂等）
    """
    CREATE TABLE IF NOT EXISTS collusion_extractions (
        id                  SERIAL PRIMARY KEY,
        created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        project_id          INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        document_id         INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        doc_type            VARCHAR(10) NOT NULL DEFAULT 'bid',
        company_info        JSONB,
        personnel_info      JSONB,
        price_info          JSONB,
        technical_features  JSONB,
        qualification_info  JSONB,
        guarantee_info      JSONB,
        metadata_info       JSONB,
        format_requirements JSONB,
        extraction_status   VARCHAR(10) NOT NULL DEFAULT 'success'
    )
    """,

    # 6. collusion_extractions 索引（幂等）
    "CREATE INDEX IF NOT EXISTS idx_collusion_extractions_project_id ON collusion_extractions(project_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_collusion_extractions_project_doc ON collusion_extractions(project_id, document_id)",
]


async def run_migration() -> None:
    dsn = get_asyncpg_dsn()
    print(f"Connecting to database...")
    conn = await asyncpg.connect(dsn)
    try:
        for i, stmt in enumerate(STATEMENTS, 1):
            stripped = stmt.strip()
            preview = stripped[:60].replace('\n', ' ')
            try:
                await conn.execute(stripped)
                print(f"[{i}/{len(STATEMENTS)}] OK: {preview}...")
            except Exception as e:
                print(f"[{i}/{len(STATEMENTS)}] ERROR: {preview}...")
                print(f"    -> {e}")
                raise
        print("\nMigration C1.5 completed successfully!")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())

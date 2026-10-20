"""一致性根治修复字段迁移(v2) - 运行方式: python migrations/run_review_consistency_v2_migration.py"""

import asyncio
import os
import sys
from pathlib import Path

script_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(script_dir)
sys.path.insert(0, backend_dir)
os.chdir(backend_dir)

from dotenv import load_dotenv

load_dotenv()

import asyncpg


def get_asyncpg_dsn() -> str:
    db_url = os.getenv("DATABASE_URL", "")
    return db_url.replace("postgresql+asyncpg://", "postgresql://")


def load_sql() -> str:
    sql_path = Path(script_dir) / "review_consistency_fields_v2.sql"
    return sql_path.read_text(encoding="utf-8")


async def run_migration() -> None:
    dsn = get_asyncpg_dsn()
    print("Connecting to database...")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(load_sql())
        print("review consistency v2 迁移完成")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())

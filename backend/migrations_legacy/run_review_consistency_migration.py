"""业绩双归因/跨类别一致性字段迁移 - 运行方式: python migrations/run_review_consistency_migration.py"""

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
    sql_path = Path(script_dir) / "review_consistency_fields.sql"
    return sql_path.read_text(encoding="utf-8")


async def run_migration() -> None:
    dsn = get_asyncpg_dsn()
    print("Connecting to database...")
    conn = await asyncpg.connect(dsn)
    try:
        sql = load_sql()
        await conn.execute(sql)
        print("review consistency 迁移完成")
    except Exception as e:
        print(f"迁移失败: {e}")
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())

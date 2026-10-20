"""bid_responses.source_pages 列长度迁移 - 运行方式: python migrations/run_source_pages_migration.py"""

import asyncio
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(script_dir)
sys.path.insert(0, backend_dir)
os.chdir(backend_dir)

from dotenv import load_dotenv
load_dotenv()

import asyncpg

STATEMENT = """
ALTER TABLE bid_responses
    ALTER COLUMN source_pages TYPE VARCHAR(500);
"""


def get_asyncpg_dsn() -> str:
    db_url = os.getenv("DATABASE_URL", "")
    return db_url.replace("postgresql+asyncpg://", "postgresql://")


async def run_migration() -> None:
    dsn = get_asyncpg_dsn()
    print("Connecting to database...")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(STATEMENT.strip())
        print("bid_responses.source_pages 已扩容为 VARCHAR(500)，迁移完成")
    except Exception as e:
        print(f"迁移失败: {e}")
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())

"""修复 projecttype 枚举大小写不一致问题

根因: SQLAlchemy 默认使用枚举名称(BID_REVIEW)存储，而非值(bid_review)。
      现有 projectstatus 使用大写名称，projecttype 需保持一致。
"""

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


def get_dsn() -> str:
    return os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


STATEMENTS = [
    # 1. 删除旧列，解除对枚举的依赖
    "ALTER TABLE projects DROP COLUMN IF EXISTS project_type",
    # 2. 删除旧的小写枚举
    "DROP TYPE IF EXISTS projecttype",
    # 3. 重建枚举，使用大写名称（与 projectstatus 保持一致）
    "CREATE TYPE projecttype AS ENUM ('BID_REVIEW', 'COLLUSION_CHECK')",
    # 4. 重新添加列，默认值使用大写名称
    "ALTER TABLE projects ADD COLUMN project_type projecttype NOT NULL DEFAULT 'BID_REVIEW'",
]


async def fix() -> None:
    conn = await asyncpg.connect(get_dsn())
    try:
        for i, stmt in enumerate(STATEMENTS, 1):
            await conn.execute(stmt)
            print(f"[{i}/{len(STATEMENTS)}] OK: {stmt[:70]}...")

        # 验证修复结果
        rows = await conn.fetch("""
            SELECT enumlabel FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = 'projecttype'
            ORDER BY e.enumsortorder
        """)
        print(f"Fixed enum values: {[r['enumlabel'] for r in rows]}")

        # 验证列存在
        col = await conn.fetchrow("""
            SELECT column_name, column_default
            FROM information_schema.columns
            WHERE table_name = 'projects' AND column_name = 'project_type'
        """)
        print(f"Column: {dict(col)}")
        print("Fix completed successfully!")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(fix())

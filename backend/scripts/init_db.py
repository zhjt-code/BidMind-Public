"""数据库前置初始化脚本

负责启用 PGVector / pg_trgm 扩展并确保其位于 public schema。
表结构和索引由 Alembic 统一管理 (alembic upgrade head)。

使用方法:
    cd backend
    python -m scripts.init_db
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.core.database import engine
from loguru import logger


async def ensure_pgvector() -> None:
    """确保 PGVector 与 pg_trgm 扩展已启用且位于 public schema。"""

    logger.info("检查 PGVector / pg_trgm 扩展...")

    try:
        async with engine.begin() as conn:
            row = (await conn.execute(text(
                "SELECT extnamespace::regnamespace AS schema "
                "FROM pg_extension WHERE extname = 'vector'"
            ))).fetchone()

            if row and row[0] != "public":
                logger.warning(
                    f"vector 扩展在错误的 schema: {row[0]}, 迁移到 public")
                await conn.execute(
                    text("DROP EXTENSION IF EXISTS vector CASCADE;"))

            await conn.execute(
                text("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;"))
            await conn.execute(
                text("CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public;"))

        logger.success(
            "PGVector / pg_trgm 扩展就绪。请运行 'alembic upgrade head' 创建/更新表结构。")

    except Exception as e:
        logger.error(f"PGVector 初始化失败: {e}")
        raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(ensure_pgvector())

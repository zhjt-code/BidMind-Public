"""执行 c2 迁移：为 projects 和 collusion_extractions 表增加 error_message 字段"""
import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://bidmind:bidmind@localhost:5432/bidmind_lite",
)


async def migrate():
    engine = create_async_engine(_DB_URL)
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'projects' AND column_name = 'error_message'"
        ))
        if not result.fetchone():
            await conn.execute(text(
                "ALTER TABLE projects ADD COLUMN error_message VARCHAR(1000) DEFAULT NULL"
            ))
            print("projects.error_message added")
        else:
            print("projects.error_message already exists")

        result = await conn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'collusion_extractions' AND column_name = 'error_message'"
        ))
        if not result.fetchone():
            await conn.execute(text(
                "ALTER TABLE collusion_extractions ADD COLUMN error_message VARCHAR(500) DEFAULT NULL"
            ))
            print("collusion_extractions.error_message added")
        else:
            print("collusion_extractions.error_message already exists")

    await engine.dispose()
    print("Migration complete.")


if __name__ == "__main__":
    asyncio.run(migrate())

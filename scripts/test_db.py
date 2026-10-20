"""数据库连接测试脚本"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core.database import engine
from sqlalchemy import text


async def test_connection():
    """测试数据库连接"""
    async with engine.connect() as conn:
        result = await conn.execute(text('SELECT version()'))
        version = result.scalar()
        print(f"PostgreSQL: {version}")
        
        # 检查 vector 扩展
        result = await conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        ext = result.scalar()
        if ext:
            print("PGVector 扩展: 已启用")
        else:
            print("PGVector 扩展: 未启用")


if __name__ == "__main__":
    print("=" * 50)
    print("数据库连接测试")
    print("=" * 50)
    asyncio.run(test_connection())
    print("\n连接测试成功!")

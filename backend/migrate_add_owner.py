"""数据库迁移脚本：添加用户认证相关表和字段

执行方式：
cd "h:\BidMind AI Lite"
.\.venv\Scripts\python backend\migrate_add_owner.py
"""

import asyncio
from sqlalchemy import text
from app.core.database import engine

async def migrate():
    """执行数据库迁移"""
    async with engine.begin() as conn:
        # 1. 检查users表是否存在，不存在则创建
        result = await conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'users'
            )
        """))
        users_exists = result.scalar()
        
        if not users_exists:
            print("创建 user_role 枚举类型...")
            # 检查枚举类型是否存在
            result = await conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM pg_type WHERE typname = 'user_role'
                )
            """))
            if not result.scalar():
                await conn.execute(text("CREATE TYPE user_role AS ENUM ('user', 'admin')"))
            
            print("创建 users 表...")
            await conn.execute(text("""
                CREATE TABLE users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    hashed_password VARCHAR(255) NOT NULL,
                    role user_role DEFAULT 'user' NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE NOT NULL,
                    last_login TIMESTAMP WITH TIME ZONE,
                    login_attempts VARCHAR(10) DEFAULT '0' NOT NULL,
                    locked_until TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
                )
            """))
            
            await conn.execute(text("CREATE INDEX ix_users_username ON users(username)"))
            await conn.execute(text("CREATE INDEX ix_users_email ON users(email)"))
            print("users 表创建成功")
        else:
            print("users 表已存在")
        
        # 2. 检查projects表是否有owner_id列
        result = await conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_name = 'projects' AND column_name = 'owner_id'
            )
        """))
        owner_id_exists = result.scalar()
        
        if not owner_id_exists:
            print("添加 owner_id 列到 projects 表...")
            await conn.execute(text("""
                ALTER TABLE projects 
                ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL
            """))
            
            # 检查索引是否存在
            result = await conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM pg_indexes WHERE indexname = 'ix_projects_owner_id'
                )
            """))
            if not result.scalar():
                await conn.execute(text("CREATE INDEX ix_projects_owner_id ON projects(owner_id)"))
            print("owner_id 列添加成功")
        else:
            print("owner_id 列已存在")
        
        print("数据库迁移完成!")

if __name__ == "__main__":
    asyncio.run(migrate())

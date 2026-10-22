-- BidMind AI Lite 数据库初始化脚本
-- 启用 PGVector 扩展

CREATE EXTENSION IF NOT EXISTS vector;

-- 确认扩展已启用
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';

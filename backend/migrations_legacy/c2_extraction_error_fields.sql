-- Phase C2 数据库迁移脚本
-- 创建时间: 2026-03-16
-- 说明: 为提取失败处理优化增加 error_message 字段
--       1. projects 表新增 error_message 字段（存储任务失败原因）
--       2. collusion_extractions 表新增 error_message 字段（存储提取失败原因）

-- ============================================================
-- 1. projects 表新增 error_message 字段
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'projects' AND column_name = 'error_message'
    ) THEN
        ALTER TABLE projects
            ADD COLUMN error_message VARCHAR(1000) DEFAULT NULL;

        RAISE NOTICE 'projects.error_message 字段已添加';
    ELSE
        RAISE NOTICE 'projects.error_message 字段已存在，跳过';
    END IF;
END
$$;


-- ============================================================
-- 2. collusion_extractions 表新增 error_message 字段
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'collusion_extractions' AND column_name = 'error_message'
    ) THEN
        ALTER TABLE collusion_extractions
            ADD COLUMN error_message VARCHAR(500) DEFAULT NULL;

        RAISE NOTICE 'collusion_extractions.error_message 字段已添加';
    ELSE
        RAISE NOTICE 'collusion_extractions.error_message 字段已存在，跳过';
    END IF;
END
$$;

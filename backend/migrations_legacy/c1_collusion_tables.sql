-- Phase C1.5 数据库迁移脚本
-- 创建时间: 2026-02-26
-- 说明: 新增串标审查功能所需的数据库变更
--       1. projects 表新增 project_type 字段
--       2. 新建 collusion_results 表
--       3. 新建 collusion_extractions 表

-- ============================================================
-- 1. projects 表新增 project_type 字段
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'projects' AND column_name = 'project_type'
    ) THEN
        -- 添加枚举类型
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'projecttype') THEN
            CREATE TYPE projecttype AS ENUM ('BID_REVIEW', 'COLLUSION_CHECK');
        END IF;

        ALTER TABLE projects
            ADD COLUMN project_type projecttype NOT NULL DEFAULT 'BID_REVIEW';

        RAISE NOTICE 'projects.project_type 字段已添加';
    ELSE
        RAISE NOTICE 'projects.project_type 字段已存在，跳过';
    END IF;
END
$$;


-- ============================================================
-- 2. 新建 collusion_results 表
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'collusion_results'
    ) THEN
        -- 创建风险等级枚举
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'collusionrisklevel') THEN
            CREATE TYPE collusionrisklevel AS ENUM (
                'high', 'medium', 'low', 'none', 'inconclusive'
            );
        END IF;

        CREATE TABLE collusion_results (
            id          SERIAL PRIMARY KEY,
            created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

            project_id  INTEGER NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,
            doc_a_id    INTEGER NOT NULL
                REFERENCES documents(id) ON DELETE CASCADE,
            doc_b_id    INTEGER NOT NULL
                REFERENCES documents(id) ON DELETE CASCADE,

            overall_risk    VARCHAR(20) NOT NULL DEFAULT 'inconclusive',
            similarity_score FLOAT,

            -- 八维度得分（NULL 表示该维度未分析）
            d1_personnel_overlap    FLOAT,
            d2_company_relation     FLOAT,
            d3_text_similarity      FLOAT,
            d4_price_pattern        FLOAT,
            d5_qualification_overlap FLOAT,
            d6_format_similarity    FLOAT,
            d7_abnormal_consistency FLOAT,
            d8_metadata_similarity  FLOAT,

            -- JSON详细数据
            findings        JSONB,
            evidence_chunks JSONB,
            llm_analysis    JSONB,

            analysis_mode   VARCHAR(20) NOT NULL DEFAULT 'algorithm_only'
        );

        -- 索引
        CREATE INDEX idx_collusion_results_project_id
            ON collusion_results(project_id);
        CREATE INDEX idx_collusion_results_doc_pair
            ON collusion_results(doc_a_id, doc_b_id);

        RAISE NOTICE 'collusion_results 表已创建';
    ELSE
        RAISE NOTICE 'collusion_results 表已存在，跳过';
    END IF;
END
$$;


-- ============================================================
-- 3. 新建 collusion_extractions 表
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'collusion_extractions'
    ) THEN
        CREATE TABLE collusion_extractions (
            id          SERIAL PRIMARY KEY,
            created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

            project_id  INTEGER NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,
            document_id INTEGER NOT NULL
                REFERENCES documents(id) ON DELETE CASCADE,
            doc_type    VARCHAR(10) NOT NULL DEFAULT 'bid',

            -- 投标文件提取结果
            company_info        JSONB,
            personnel_info      JSONB,
            price_info          JSONB,
            technical_features  JSONB,
            qualification_info  JSONB,
            guarantee_info      JSONB,
            metadata_info       JSONB,

            -- 招标文件专用（白名单过滤器）
            format_requirements JSONB,

            extraction_status   VARCHAR(10) NOT NULL DEFAULT 'success'
        );

        -- 索引
        CREATE INDEX idx_collusion_extractions_project_id
            ON collusion_extractions(project_id);
        CREATE UNIQUE INDEX idx_collusion_extractions_project_doc
            ON collusion_extractions(project_id, document_id);

        RAISE NOTICE 'collusion_extractions 表已创建';
    ELSE
        RAISE NOTICE 'collusion_extractions 表已存在，跳过';
    END IF;
END
$$;

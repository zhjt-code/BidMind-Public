-- Phase C6: 串标嫌疑群组表
-- 幂等脚本：检查表是否存在后再创建

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'collusion_groups') THEN
        CREATE TABLE collusion_groups (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            group_index INTEGER NOT NULL,
            member_doc_ids JSONB NOT NULL,
            member_names JSONB NOT NULL,
            risk_level VARCHAR(20) NOT NULL DEFAULT 'medium',
            aggregated_evidence JSONB,
            role_analysis JSONB,
            network_description TEXT,
            collusion_strategy TEXT,
            investigation_focus JSONB,
            related_pair_ids JSONB,
            llm_group_analysis JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        CREATE INDEX idx_collusion_groups_project_id ON collusion_groups(project_id);
        CREATE UNIQUE INDEX uq_cg_project_group ON collusion_groups(project_id, group_index);

        RAISE NOTICE 'Created table: collusion_groups';
    ELSE
        RAISE NOTICE 'Table collusion_groups already exists, skipping';
    END IF;
END $$;

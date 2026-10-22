-- 业绩双归因与跨类别一致性字段迁移
-- 创建时间: 2026-03-14

-- requirements 扩展
ALTER TABLE requirements
    ADD COLUMN IF NOT EXISTS topic VARCHAR(50);

ALTER TABLE requirements
    ADD COLUMN IF NOT EXISTS subject_scope VARCHAR(20);

ALTER TABLE requirements
    ADD COLUMN IF NOT EXISTS canonical_key VARCHAR(255);

ALTER TABLE requirements
    ADD COLUMN IF NOT EXISTS main_requirement_id INTEGER REFERENCES requirements(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_requirements_canonical_key
    ON requirements(canonical_key);

CREATE INDEX IF NOT EXISTS idx_requirements_main_requirement_id
    ON requirements(main_requirement_id);

-- bid_responses 扩展
ALTER TABLE bid_responses
    ADD COLUMN IF NOT EXISTS completeness VARCHAR(20);

ALTER TABLE bid_responses
    ADD COLUMN IF NOT EXISTS missing_elements TEXT;

ALTER TABLE bid_responses
    ADD COLUMN IF NOT EXISTS linked_to INTEGER;

ALTER TABLE bid_responses
    ADD COLUMN IF NOT EXISTS evidence_anchor VARCHAR(255);

-- review_results 扩展
ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS canonical_key VARCHAR(255);

ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS attribution_type VARCHAR(20);

ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS consistency_flag VARCHAR(20);

ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS main_requirement_id INTEGER REFERENCES requirements(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_review_results_canonical_key
    ON review_results(canonical_key);

CREATE INDEX IF NOT EXISTS idx_review_results_main_requirement_id
    ON review_results(main_requirement_id);

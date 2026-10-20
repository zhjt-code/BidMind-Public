-- 一致性根治修复字段迁移（v2）
-- 创建时间: 2026-03-14

ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS fact_key VARCHAR(255);

ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS family_type VARCHAR(30);

ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS evidence_relation VARCHAR(20);

ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS reconcile_note TEXT;

CREATE INDEX IF NOT EXISTS idx_review_results_fact_key
    ON review_results(fact_key);

CREATE INDEX IF NOT EXISTS idx_review_results_family_type
    ON review_results(family_type);

-- 关联性审查字段迁移
-- 创建时间: 2026-03-15

ALTER TABLE bid_responses
    ADD COLUMN IF NOT EXISTS relevance_score INTEGER;

ALTER TABLE bid_responses
    ADD COLUMN IF NOT EXISTS relevance_note VARCHAR(500);

ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS relevance_score INTEGER;

ALTER TABLE review_results
    ADD COLUMN IF NOT EXISTS relevance_note VARCHAR(500);

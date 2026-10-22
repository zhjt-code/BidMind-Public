-- bid_responses.source_pages 列长度扩容
-- 创建时间: 2026-03-14
-- 说明: source_pages 原为 VARCHAR(100)，格式要求类响应可能引用 29+ 个页码，
--       逗号拼接后超 100 字符触发 StringDataRightTruncationError
-- 修复: 改为 VARCHAR(500)，并在 bid_extractor 中增加防御性截断

ALTER TABLE bid_responses
    ALTER COLUMN source_pages TYPE VARCHAR(500);

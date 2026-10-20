-- 事实唯一 + 多视图映射（v1）
-- 创建时间: 2026-03-14

CREATE TABLE IF NOT EXISTS review_facts (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    fact_uid VARCHAR(255) NOT NULL,
    topic VARCHAR(50),
    family_type VARCHAR(30),
    anchor_text TEXT,
    source_pages VARCHAR(500),
    confidence FLOAT,
    primary_compliance_level VARCHAR(20),
    primary_score INTEGER,
    primary_risk_level VARCHAR(20),
    primary_note TEXT,
    status VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_review_fact_project_uid UNIQUE (project_id, fact_uid)
);

CREATE INDEX IF NOT EXISTS idx_review_facts_project_id ON review_facts(project_id);
CREATE INDEX IF NOT EXISTS idx_review_facts_fact_uid ON review_facts(fact_uid);

CREATE TABLE IF NOT EXISTS review_fact_links (
    id SERIAL PRIMARY KEY,
    fact_id INTEGER NOT NULL REFERENCES review_facts(id) ON DELETE CASCADE,
    requirement_id INTEGER REFERENCES requirements(id) ON DELETE CASCADE,
    bid_response_id INTEGER REFERENCES bid_responses(id) ON DELETE CASCADE,
    category VARCHAR(30),
    subject_scope VARCHAR(20),
    link_role VARCHAR(20) NOT NULL DEFAULT 'pair',
    evidence_anchor VARCHAR(255),
    source_pages VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_fact_links_fact_id ON review_fact_links(fact_id);
CREATE INDEX IF NOT EXISTS idx_review_fact_links_requirement_id ON review_fact_links(requirement_id);
CREATE INDEX IF NOT EXISTS idx_review_fact_links_bid_response_id ON review_fact_links(bid_response_id);

CREATE TABLE IF NOT EXISTS review_fact_judgements (
    id SERIAL PRIMARY KEY,
    fact_id INTEGER NOT NULL REFERENCES review_facts(id) ON DELETE CASCADE,
    requirement_id INTEGER REFERENCES requirements(id) ON DELETE CASCADE,
    review_result_id INTEGER REFERENCES review_results(id) ON DELETE CASCADE,
    view_category VARCHAR(30),
    compliance_level VARCHAR(20),
    score INTEGER,
    risk_level VARCHAR(20),
    is_primary INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_fact_judgements_fact_id ON review_fact_judgements(fact_id);
CREATE INDEX IF NOT EXISTS idx_review_fact_judgements_requirement_id ON review_fact_judgements(requirement_id);
CREATE INDEX IF NOT EXISTS idx_review_fact_judgements_review_result_id ON review_fact_judgements(review_result_id);

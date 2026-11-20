-- KB "Resolution Units": one row per KB article merged with its runbook play.
-- Source of truth is knowledge/units/*.json, loaded by scripts/kb_load.py.
-- `embedding` is an unconstrained vector so it follows EMBEDDING_DIMENSIONS (no index needed at ~13 rows).
CREATE TABLE IF NOT EXISTS kb_units (
    unit_id           TEXT PRIMARY KEY,
    article_id        TEXT NOT NULL UNIQUE,
    root_cause_codes  TEXT[] NOT NULL,
    runbook           TEXT NOT NULL,
    play              TEXT NOT NULL,
    module            TEXT NOT NULL,
    category_path     TEXT NOT NULL,
    title             TEXT NOT NULL,
    match_text        TEXT NOT NULL,          -- what gets embedded / keyword-matched: title + keywords + symptoms + cause
    content           JSONB NOT NULL,         -- full card, shown verbatim to agents
    version           TEXT NOT NULL,
    review_status     TEXT NOT NULL DEFAULT 'draft-pending-sme-review',
    content_hash      TEXT NOT NULL,
    kb_version        TEXT,
    embedding         vector,
    tsv               tsvector GENERATED ALWAYS AS (to_tsvector('english', match_text)) STORED,
    loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_units_tsv ON kb_units USING gin (tsv);

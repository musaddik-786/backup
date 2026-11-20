-- =============================================================================
-- Knowledge Ops Agent - Knowledge Store schema (PostgreSQL + pgvector)
--
-- One table PER SOURCE (tickets / chat+RCA / release notes / runbooks),
-- each with its own vector(1536) embedding column and its own JSONB
-- source_metadata column for fields that only make sense for that source
-- (e.g. a ticket's priority/assignee_team vs. a runbook's owner_team/steps).
--
-- The 4 tables share the same common "spine" of columns (taxonomy tags,
-- structured_sop, embedding, curation status) so a query can treat them
-- uniformly via UNION ALL -- see knowledge_store.search_similar(), which
-- is how the "One Knowledge Base, Four Tier Agents" retrieval pattern
-- works across tables instead of across rows of one table.
--
-- Design choices (same reasoning as before, now applied per table):
--   - TEXT + CHECK constraints (not native Postgres ENUM types) for the
--     taxonomy columns, since the taxonomy will evolve over time and
--     ALTER TYPE ... ADD VALUE on a live enum is operationally painful.
--   - embedding vector(1536) matches Azure OpenAI text-embedding-3-large
--     (see structuring.py), same model/dimension as the Underwriting
--     Solution project. No ivfflat/hnsw index is created yet (see db/migrations/002_vector_indexes.sql);
--     pgvector index types support up to 2000 dimensions, so 1536 is indexable;
--     until then queries do a plain sequential scan (ORDER BY embedding <=> query),
--     same as query_retriever_pgvector.py in Underwriting Solution. Fine
--     at this KB's current scale; revisit (e.g. halfvec, or truncating
--     the embedding dimension) if the table grows into the tens of
--     thousands of rows.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- -----------------------------------------------------------------------
-- Shared taxonomy CHECK list, repeated per table below (Postgres has no
-- "shared domain constraint" shortcut cheaper than this for CHECK IN).
-- -----------------------------------------------------------------------

-- =========================== 1) TICKETS =================================
CREATE TABLE IF NOT EXISTS ticket_records (
    record_id           TEXT PRIMARY KEY,
    source_ref_id        TEXT NOT NULL,          -- original ticket_id

    module               TEXT NOT NULL CHECK (module IN (
                            'PolicyCenter', 'ClaimCenter', 'BillingCenter',
                            'ContactManager', 'Rating/Gosu', 'IntegrationGateway',
                            'DigitalPortal', 'DataPlatform'
                         )),
    category             TEXT NOT NULL CHECK (category IN (
                            'PerformanceDegradation', 'IntegrationFailure', 'DataCorruption',
                            'ConfigurationError', 'UpgradeRegression', 'AuthenticationAccess',
                            'BatchJobFailure', 'UIRenderingBug', 'Licensing'
                         )),
    severity             TEXT NOT NULL CHECK (severity IN (
                            'Emergency', 'High', 'Normal', 'Low'
                         )),
    resolution_type      TEXT CHECK (resolution_type IN (
                            'ConfigChange', 'CodeFix', 'PatchApplied', 'RestartRecycle',
                            'DataRemediation', 'VendorEscalation', 'Workaround',
                            'UserErrorClosed', 'NoActionByDesign'
                         )),
    tagging_confidence   REAL NOT NULL CHECK (tagging_confidence BETWEEN 0 AND 1),
    tagging_reasoning    TEXT,                    -- LLM's own explanation for category/resolution_type picks

    title                TEXT NOT NULL,
    raw_text             TEXT NOT NULL,
    structured_sop       TEXT,
    resolution           TEXT,                   -- resolution_text: synthesized root-cause + fix narrative
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(1536),         -- nullable: populated by step4_store_embeddings.py
    source_url           TEXT,

    -- ticket-specific extras: priority, assignee_team, created_at, closed_at
    source_metadata      JSONB,

    curation_status      TEXT NOT NULL CHECK (curation_status IN (
                            'PendingReview', 'AutoApproved', 'SMEApproved',
                            'SMERejected', 'NeedsRetag'
                         )),
    curation_notes       TEXT,
    kb_version           TEXT,
    ingested_at          TIMESTAMPTZ NOT NULL,
    upserted_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ticket_records_module ON ticket_records (module);
CREATE INDEX IF NOT EXISTS idx_ticket_records_category ON ticket_records (category);
CREATE INDEX IF NOT EXISTS idx_ticket_records_severity ON ticket_records (severity);
CREATE INDEX IF NOT EXISTS idx_ticket_records_curation_status ON ticket_records (curation_status);

-- ========================= 2) CHAT / RCA =================================
CREATE TABLE IF NOT EXISTS chat_rca_records (
    record_id           TEXT PRIMARY KEY,
    source_ref_id        TEXT NOT NULL,          -- original chat/RCA record_id

    module               TEXT NOT NULL CHECK (module IN (
                            'PolicyCenter', 'ClaimCenter', 'BillingCenter',
                            'ContactManager', 'Rating/Gosu', 'IntegrationGateway',
                            'DigitalPortal', 'DataPlatform'
                         )),
    category             TEXT NOT NULL CHECK (category IN (
                            'PerformanceDegradation', 'IntegrationFailure', 'DataCorruption',
                            'ConfigurationError', 'UpgradeRegression', 'AuthenticationAccess',
                            'BatchJobFailure', 'UIRenderingBug', 'Licensing'
                         )),
    severity             TEXT NOT NULL CHECK (severity IN (
                            'Emergency', 'High', 'Normal', 'Low'
                         )),
    resolution_type      TEXT NOT NULL CHECK (resolution_type IN (
                            'ConfigChange', 'CodeFix', 'PatchApplied', 'RestartRecycle',
                            'DataRemediation', 'VendorEscalation', 'Workaround',
                            'UserErrorClosed', 'NoActionByDesign'
                         )),
    tagging_confidence   REAL NOT NULL CHECK (tagging_confidence BETWEEN 0 AND 1),
    tagging_reasoning    TEXT,                    -- LLM's own explanation for category/resolution_type picks

    title                TEXT NOT NULL,
    raw_text             TEXT NOT NULL,
    structured_sop       TEXT NOT NULL,
    resolution           TEXT,                   -- resolution_text: synthesized root-cause + fix narrative
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(1536),         -- nullable: populated by step4_store_embeddings.py
    source_url           TEXT,

    -- chat/RCA-specific extras: record_type, incident_id, participants,
    -- root_cause, corrective_actions
    source_metadata      JSONB,

    curation_status      TEXT NOT NULL CHECK (curation_status IN (
                            'PendingReview', 'AutoApproved', 'SMEApproved',
                            'SMERejected', 'NeedsRetag'
                         )),
    curation_notes       TEXT,
    kb_version           TEXT NOT NULL,
    ingested_at          TIMESTAMPTZ NOT NULL,
    upserted_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_rca_records_module ON chat_rca_records (module);
CREATE INDEX IF NOT EXISTS idx_chat_rca_records_category ON chat_rca_records (category);
CREATE INDEX IF NOT EXISTS idx_chat_rca_records_severity ON chat_rca_records (severity);
CREATE INDEX IF NOT EXISTS idx_chat_rca_records_curation_status ON chat_rca_records (curation_status);

-- ====================== 3) GUIDEWIRE RELEASE NOTES ========================
CREATE TABLE IF NOT EXISTS release_note_records (
    record_id           TEXT PRIMARY KEY,
    source_ref_id        TEXT NOT NULL,          -- original release_id / issue id

    module               TEXT NOT NULL CHECK (module IN (
                            'PolicyCenter', 'ClaimCenter', 'BillingCenter',
                            'ContactManager', 'Rating/Gosu', 'IntegrationGateway',
                            'DigitalPortal', 'DataPlatform'
                         )),
    category             TEXT NOT NULL CHECK (category IN (
                            'PerformanceDegradation', 'IntegrationFailure', 'DataCorruption',
                            'ConfigurationError', 'UpgradeRegression', 'AuthenticationAccess',
                            'BatchJobFailure', 'UIRenderingBug', 'Licensing'
                         )),
    severity             TEXT NOT NULL CHECK (severity IN (
                            'Emergency', 'High', 'Normal', 'Low'
                         )),
    resolution_type      TEXT NOT NULL CHECK (resolution_type IN (
                            'ConfigChange', 'CodeFix', 'PatchApplied', 'RestartRecycle',
                            'DataRemediation', 'VendorEscalation', 'Workaround',
                            'UserErrorClosed', 'NoActionByDesign'
                         )),
    tagging_confidence   REAL NOT NULL CHECK (tagging_confidence BETWEEN 0 AND 1),
    tagging_reasoning    TEXT,                    -- LLM's own explanation for category/resolution_type picks

    title                TEXT NOT NULL,
    raw_text             TEXT NOT NULL,
    structured_sop       TEXT NOT NULL,
    resolution           TEXT,                   -- resolution_text: synthesized root-cause + fix narrative
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(1536),         -- nullable: populated by step4_store_embeddings.py
    source_url           TEXT,                   -- real docs.guidewire.com link, when known

    -- release-note-specific extras: note_type, product, affected_modules
    source_metadata      JSONB,

    curation_status      TEXT NOT NULL CHECK (curation_status IN (
                            'PendingReview', 'AutoApproved', 'SMEApproved',
                            'SMERejected', 'NeedsRetag'
                         )),
    curation_notes       TEXT,
    kb_version           TEXT NOT NULL,
    ingested_at          TIMESTAMPTZ NOT NULL,
    upserted_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_release_note_records_module ON release_note_records (module);
CREATE INDEX IF NOT EXISTS idx_release_note_records_category ON release_note_records (category);
CREATE INDEX IF NOT EXISTS idx_release_note_records_severity ON release_note_records (severity);
CREATE INDEX IF NOT EXISTS idx_release_note_records_curation_status ON release_note_records (curation_status);

-- ========================= 4) RUNBOOKS / SOPS =============================
CREATE TABLE IF NOT EXISTS runbook_records (
    record_id           TEXT PRIMARY KEY,
    source_ref_id        TEXT NOT NULL,          -- original runbook_id

    module               TEXT NOT NULL CHECK (module IN (
                            'PolicyCenter', 'ClaimCenter', 'BillingCenter',
                            'ContactManager', 'Rating/Gosu', 'IntegrationGateway',
                            'DigitalPortal', 'DataPlatform'
                         )),
    category             TEXT NOT NULL CHECK (category IN (
                            'PerformanceDegradation', 'IntegrationFailure', 'DataCorruption',
                            'ConfigurationError', 'UpgradeRegression', 'AuthenticationAccess',
                            'BatchJobFailure', 'UIRenderingBug', 'Licensing'
                         )),
    severity             TEXT NOT NULL CHECK (severity IN (
                            'Emergency', 'High', 'Normal', 'Low'
                         )),
    resolution_type      TEXT NOT NULL CHECK (resolution_type IN (
                            'ConfigChange', 'CodeFix', 'PatchApplied', 'RestartRecycle',
                            'DataRemediation', 'VendorEscalation', 'Workaround',
                            'UserErrorClosed', 'NoActionByDesign'
                         )),
    tagging_confidence   REAL NOT NULL CHECK (tagging_confidence BETWEEN 0 AND 1),
    tagging_reasoning    TEXT,                    -- LLM's own explanation for category/resolution_type picks

    title                TEXT NOT NULL,
    raw_text             TEXT NOT NULL,
    structured_sop       TEXT NOT NULL,
    resolution           TEXT,                   -- resolution_text: synthesized root-cause + fix narrative
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(1536),         -- nullable: populated by step4_store_embeddings.py
    source_url           TEXT,

    -- runbook-specific extras: owner_team, trigger_condition, steps
    source_metadata      JSONB,

    curation_status      TEXT NOT NULL CHECK (curation_status IN (
                            'PendingReview', 'AutoApproved', 'SMEApproved',
                            'SMERejected', 'NeedsRetag'
                         )),
    curation_notes       TEXT,
    kb_version           TEXT NOT NULL,
    ingested_at          TIMESTAMPTZ NOT NULL,
    upserted_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_runbook_records_module ON runbook_records (module);
CREATE INDEX IF NOT EXISTS idx_runbook_records_category ON runbook_records (category);
CREATE INDEX IF NOT EXISTS idx_runbook_records_severity ON runbook_records (severity);
CREATE INDEX IF NOT EXISTS idx_runbook_records_curation_status ON runbook_records (curation_status);

-- ========================= COLUMN MIGRATIONS ==============================
-- CREATE TABLE IF NOT EXISTS above does nothing to a table that already
-- exists from a previous run of this script -- so when a new column is
-- added here, it also needs an idempotent ALTER TABLE below, or anyone
-- re-running step2_create_tables.py against an already-created database
-- never actually gets the new column. Safe to run every time: a no-op
-- once the column exists.
ALTER TABLE ticket_records       ADD COLUMN IF NOT EXISTS tagging_reasoning TEXT;
ALTER TABLE chat_rca_records     ADD COLUMN IF NOT EXISTS tagging_reasoning TEXT;
ALTER TABLE release_note_records ADD COLUMN IF NOT EXISTS tagging_reasoning TEXT;
ALTER TABLE runbook_records      ADD COLUMN IF NOT EXISTS tagging_reasoning TEXT;

-- content_hash: every record is now written into its table as soon as
-- it's ingested (curation_status='PendingReview', embedding still NULL)
-- so a human reviewer can see and act on it via sme_review.py. Once
-- approved/rejected/retagged, that decision needs to be remembered for
-- duplicate-run prevention (ingestion_state) WITHOUT re-reading the raw
-- JSON -- so the hash travels with the row itself.
ALTER TABLE ticket_records       ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE chat_rca_records     ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE release_note_records ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE runbook_records      ADD COLUMN IF NOT EXISTS content_hash TEXT;

-- resolution: the LLM-synthesized "how it was fixed" narrative
-- (NormalizedRecord.resolution_text) was already folded into
-- structured_sop's markdown, but a reviewer/agent querying the KB may
-- want this root-cause/fix narrative on its own, outside the full SOP
-- markdown -- so it's stored as its own column too.
ALTER TABLE ticket_records       ADD COLUMN IF NOT EXISTS resolution TEXT;

-- ticket_records: resolution_type, resolution, structured_sop and kb_version
-- are all optional (open / unresolved tickets have none of them). Relax NOT NULL on existing DBs;
-- DROP NOT NULL is a no-op if the column is already nullable.
ALTER TABLE ticket_records ALTER COLUMN resolution_type DROP NOT NULL;
ALTER TABLE ticket_records ALTER COLUMN resolution      DROP NOT NULL;
ALTER TABLE ticket_records ALTER COLUMN structured_sop DROP NOT NULL;
ALTER TABLE ticket_records ALTER COLUMN kb_version     DROP NOT NULL;
ALTER TABLE chat_rca_records     ADD COLUMN IF NOT EXISTS resolution TEXT;
ALTER TABLE release_note_records ADD COLUMN IF NOT EXISTS resolution TEXT;
ALTER TABLE runbook_records      ADD COLUMN IF NOT EXISTS resolution TEXT;

-- Same story for the embedding width: a table created back when this
-- column was vector(512) stays vector(512) forever unless widened here.
-- Drop the old (now invalid anyway -- 1536 > pgvector's 2000-dim index
-- limit) ivfflat indexes first, since an index on the column can block
-- ALTER COLUMN TYPE.
DROP INDEX IF EXISTS idx_ticket_records_embedding;
DROP INDEX IF EXISTS idx_chat_rca_records_embedding;
DROP INDEX IF EXISTS idx_release_note_records_embedding;
DROP INDEX IF EXISTS idx_runbook_records_embedding;

-- "USING NULL" explicitly discards any existing (stale, TF-IDF-era
-- 512-dim) embedding values instead of trying to cast them into the new
-- width, which Postgres can't do -- a vector's dimension isn't just a
-- type label, it's the actual stored length. Rows get embedding=NULL
-- after this, same as a freshly-inserted row; step4_store_embeddings.py
-- (or pipeline.py) regenerates them on the next run either way.
ALTER TABLE ticket_records       ALTER COLUMN embedding TYPE vector(1536) USING NULL;
ALTER TABLE chat_rca_records     ALTER COLUMN embedding TYPE vector(1536) USING NULL;
ALTER TABLE release_note_records ALTER COLUMN embedding TYPE vector(1536) USING NULL;
ALTER TABLE runbook_records      ALTER COLUMN embedding TYPE vector(1536) USING NULL;

-- ===================== SHARED GOVERNANCE TABLES ===========================

-- Audit trail for the SME Curation Gate -- logged regardless of which of
-- the 4 tables (or none, if held back) the record ended up in.
CREATE TABLE IF NOT EXISTS curation_audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    record_id           TEXT NOT NULL,
    source_type         TEXT NOT NULL CHECK (source_type IN (
                            'HistoricalTicket', 'ChatOrRCAReport',
                            'GuidewireReleaseNote', 'RunbookOrSOP'
                         )),
    tagging_confidence  REAL NOT NULL,
    curation_status     TEXT NOT NULL,
    curation_notes      TEXT,
    reviewed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_curation_audit_log_record_id ON curation_audit_log (record_id);

-- Tracks each ingestion/refresh run across all 4 sources at once.
CREATE TABLE IF NOT EXISTS kb_refresh_log (
    kb_version          TEXT PRIMARY KEY,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    records_ingested    INT NOT NULL,
    records_approved    INT NOT NULL,
    records_held_back   INT NOT NULL
);

-- Per-record content hash from the last run that actually re-tagged/
-- re-embedded it. Lets ingest_all() skip the (expensive) LLM tagging call
-- entirely for a record whose raw source fields haven't changed since the
-- last run -- see ingestion.py's `existing_hashes` parameter and
-- knowledge_store.fetch_ingestion_hashes()/upsert_ingestion_hashes().
-- Only AutoApproved/SMEApproved records are recorded here on purpose: a
-- NeedsRetag record is deliberately left out so it keeps getting retried
-- on every run (not silently skipped forever) until it changes or is
-- approved.
CREATE TABLE IF NOT EXISTS ingestion_state (
    record_id           TEXT PRIMARY KEY,
    source_type         TEXT NOT NULL CHECK (source_type IN (
                            'HistoricalTicket', 'ChatOrRCAReport',
                            'GuidewireReleaseNote', 'RunbookOrSOP'
                         )),
    content_hash        TEXT NOT NULL,
    last_ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    kb_version          TEXT NOT NULL
);
-- =============================================================================
-- Knowledge Ops Agent - Knowledge Store schema (PostgreSQL + pgvector)
--
-- One table PER SOURCE (tickets / chat+RCA / release notes / runbooks),
-- each with its own vector(3072) embedding column and its own JSONB
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
--   - embedding vector(3072) matches Azure OpenAI text-embedding-3-large
--     (see structuring.py), same model/dimension as the Underwriting
--     Solution project. NO ivfflat/hnsw index on these columns: pgvector's
--     index types only support up to 2000 dimensions, so at 3072 dims we
--     can only do a plain sequential scan (ORDER BY embedding <=> query),
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
                            'SEV1-Critical', 'SEV2-High', 'SEV3-Medium', 'SEV4-Low'
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
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(3072),         -- nullable: populated by step4_store_embeddings.py
    source_url           TEXT,

    -- ticket-specific extras: priority, assignee_team, created_at, closed_at
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
                            'SEV1-Critical', 'SEV2-High', 'SEV3-Medium', 'SEV4-Low'
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
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(3072),         -- nullable: populated by step4_store_embeddings.py
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
                            'SEV1-Critical', 'SEV2-High', 'SEV3-Medium', 'SEV4-Low'
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
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(3072),         -- nullable: populated by step4_store_embeddings.py
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
                            'SEV1-Critical', 'SEV2-High', 'SEV3-Medium', 'SEV4-Low'
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
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(3072),         -- nullable: populated by step4_store_embeddings.py
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

-- Same story for the embedding width: a table created back when this
-- column was vector(512) stays vector(512) forever unless widened here.
-- Drop the old (now invalid anyway -- 3072 > pgvector's 2000-dim index
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
ALTER TABLE ticket_records       ALTER COLUMN embedding TYPE vector(3072) USING NULL;
ALTER TABLE chat_rca_records     ALTER COLUMN embedding TYPE vector(3072) USING NULL;
ALTER TABLE release_note_records ALTER COLUMN embedding TYPE vector(3072) USING NULL;
ALTER TABLE runbook_records      ALTER COLUMN embedding TYPE vector(3072) USING NULL;

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
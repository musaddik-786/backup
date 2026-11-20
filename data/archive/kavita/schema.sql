
CREATE EXTENSION IF NOT EXISTS vector;


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

    title                TEXT NOT NULL,
    raw_text             TEXT NOT NULL,
    structured_sop       TEXT NOT NULL,
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(512),         -- nullable: populated by step4_store_embeddings.py
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

CREATE INDEX IF NOT EXISTS idx_ticket_records_embedding
    ON ticket_records USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
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

    title                TEXT NOT NULL,
    raw_text             TEXT NOT NULL,
    structured_sop       TEXT NOT NULL,
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(512),         -- nullable: populated by step4_store_embeddings.py
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

CREATE INDEX IF NOT EXISTS idx_chat_rca_records_embedding
    ON chat_rca_records USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
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

    title                TEXT NOT NULL,
    raw_text             TEXT NOT NULL,
    structured_sop       TEXT NOT NULL,
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(512),         -- nullable: populated by step4_store_embeddings.py
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

CREATE INDEX IF NOT EXISTS idx_release_note_records_embedding
    ON release_note_records USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
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

    title                TEXT NOT NULL,
    raw_text             TEXT NOT NULL,
    structured_sop       TEXT NOT NULL,
    guidewire_version    TEXT,
    event_date           DATE NOT NULL,
    embedding            vector(512),         -- nullable: populated by step4_store_embeddings.py
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

CREATE INDEX IF NOT EXISTS idx_runbook_records_embedding
    ON runbook_records USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_runbook_records_module ON runbook_records (module);
CREATE INDEX IF NOT EXISTS idx_runbook_records_category ON runbook_records (category);
CREATE INDEX IF NOT EXISTS idx_runbook_records_severity ON runbook_records (severity);
CREATE INDEX IF NOT EXISTS idx_runbook_records_curation_status ON runbook_records (curation_status);

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
 
-- Run AFTER embeddings are regenerated at 1536 dims (HNSW supports <= 2000 dims).
CREATE INDEX IF NOT EXISTS idx_ticket_records_embedding       ON ticket_records       USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chat_rca_records_embedding     ON chat_rca_records     USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_release_note_records_embedding ON release_note_records USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_runbook_records_embedding      ON runbook_records      USING hnsw (embedding vector_cosine_ops);

# Consolidation notes
Sources: Sai (main), Kavita, Musaddique, Rohan worktrees (copied, originals untouched). Musaddique's backend is authoritative.
- Kavita `knowledge/` backend/frontend/tests dropped; her xlsx, JSON and 512-dim schema kept in `data/archive/kavita/`.
- Data merged by ID (no loss): tickets 60+14=74 (no overlap), release_notes 9+6=15. chat_rca/runbooks identical.
- UPDATE: retrieval/backend now use `EMBEDDING_DIMENSIONS` (default 3072) against the existing `Knowledgeops_gw`; the 1536 DB/schema is an optional path (set `EMBEDDING_DIMENSIONS=1536` and PG_DATABASE accordingly).
- (Earlier) Embeddings moved to 1536 (`dimensions=1536` on text-embedding-3-large). Done in a NEW database via `db/create_new_db.py`; the existing DB is not modified. Re-embed required in the new DB.
- `.env` copied from Musaddique (git-ignored); `sample.env` documents keys. Both `PG_*` and `AZURE_PG_*` still needed.
- UI Express API ported to FastAPI (`app/routers/tickets.py`); legacy server kept for reference.
## Open items / next actions
1. 
2. Unify env names (PG_* vs AZURE_PG_*), single config module.
3. Run `create_new_db.py` + steps 3/4 against Azure (not executed here), then HNSW indexes.
4. Port SME approval (`sme_approve`) to API/UI; wire retrieval into UI.
5. CI/CD to App Service, Key Vault/managed identity, Entra ID auth, App Insights.

## Update: single ticket store
Retrieval no longer has its own table/ingestion (`retrieval_agent/ingest.py` and `retrieval_agent_ticket_records` logic removed). It reads `ticket_records` (AutoApproved/SMEApproved + embedding). The empty `retrieval_agent_ticket_records` table created earlier in `Knowledgeops_gw_1536` has been dropped. Linked RCA/runbook evidence enrichment (previously from ingest) is not carried over; tickets only for now.

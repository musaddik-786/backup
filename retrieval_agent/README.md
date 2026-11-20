# Similar-Ticket Retrieval Agent

Finds historical tickets similar to a new one. **There is one ticket store: the knowledge base's
`ticket_records` table.** The agent has no table or ingestion of its own.

## Flow
1. Candidates = `ticket_records` rows with `curation_status` in (`AutoApproved`, `SMEApproved`) and an embedding
   (`database.py`; the row is mapped to the pipeline shape by `to_candidate`: module -> system, raw_text -> description,
   category -> ticket_type, resolution_type -> fix_path, title -> summary).
2. `pipeline.py` ranks them with BM25 + vector cosine fused by reciprocal rank fusion, plus small system/recency boosts.
3. The chat model reranks the top candidates and returns match strength, why similar, what differs and reusable info.
   Matches can only be IDs that were retrieved.

Embeddings are created by `backend/step4_store_embeddings.py`. Dimension is the `EMBEDDING_DIMENSIONS` env var (default 3072, matching the existing `Knowledgeops_gw` `vector(3072)` column); query and stored vectors must match it.
New tickets are embedded at query time with the same model/dimension. Rows with another dimension (for example the old
3072-dim ones) are rejected with a clear error.

## Config
Reads the repository root `.env` (`PG_*` or `AZURE_PG_*`, `AZURE_OPENAI_*`). Optional: `RETRIEVAL_MIN_SCORE` (0.68),
`RETRIEVAL_MIN_BM25_SCORE` (1.0), `RETRIEVAL_RRF_CONSTANT`, `RETRIEVAL_TEMPORAL_BOOST_DAYS`, `RETRIEVAL_TEMPORAL_BOOST_MAGNITUDE`.

## Run / test
```bash
uvicorn app.main:app --port 8000      # POST /retrieve and POST /api/tickets/similar
python -m unittest retrieval_agent.tests -v   # database-free
```
Not implemented yet: searching chat/RCA, release-note and runbook sources (tickets only for now).

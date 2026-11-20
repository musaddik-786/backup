# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Similar-Ticket Retrieval Agent** that finds matching historical support tickets for new Guidewire-related service desk issues. It uses hybrid search (BM25 + semantic embeddings) with reciprocal rank fusion (RRF), applies ITIL classification, and returns LLM-ranked candidates with reasoning.

The agent reads approved, embedded tickets from the knowledge base's `ticket_records` table (Azure PostgreSQL + pgvector; embeddings come from `backend/step4_store_embeddings.py`) and exposes a FastAPI endpoint (`POST /retrieve`) that accepts incoming tickets and returns the top 3-5 similar past tickets with confidence assessments.

**See** `Retrieval_Feature_Prompt.md` for the full specification, evaluation criteria, and ITIL classification rules embedded in the LLM system prompt.

## Key Files and Responsibilities

- **main.py** — FastAPI server (`POST /retrieve` endpoint). Takes a ticket (ID, description, system, severity, etc.) and returns JSON assessment with similar-ticket matches.
- **pipeline.py** — Core retrieval logic: BM25 keyword scoring, vector-embedding ranking, reciprocal rank fusion (RRF), system/type boosts, LLM reranking, and final JSON formatting.
- **database.py** — Read-only fetch of approved, embedded tickets from the KB `ticket_records` table (the single ticket store) and mapping to the pipeline's record shape.
- **providers.py** — Configuration loader (from `.env`), Azure OpenAI client (embeddings and chat completion), and Settings dataclass.
- **tests.py** — Unit tests covering row mapping, BM25 scoring, ranking, and end-to-end retrieval without a database.

## Configuration

Create or update `.env` with:

```
# PostgreSQL (Azure)
AZURE_PG_HOST=...
AZURE_PG_PORT=5432
AZURE_PG_DATABASE=...
AZURE_PG_USER=...
AZURE_PG_PASSWORD=...
AZURE_PG_SSLMODE=require

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_API_VERSION=...
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=...  # Must support 1536-dimensional embeddings
AZURE_OPENAI_CHAT_DEPLOYMENT=...  # For LLM reranking

# Optional retrieval thresholds (defaults shown)
RETRIEVAL_MIN_SCORE=0.68
RETRIEVAL_MIN_BM25_SCORE=1.0
RETRIEVAL_RRF_CONSTANT=60
```

Environment values are never printed.

## Commands

All commands run from the **repository root**  using the project's Python environment.

### Start the API server

```bash
python -m retrieval_agent.main
```

Runs FastAPI on `127.0.0.1:8000`. The endpoint is `POST /retrieve`; see `main.py` for input schema.

### Run all unit tests

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest retrieval_agent.tests -v
```

Tests row mapping, BM25, ranking, and end-to-end retrieval. No database required (all mocked).


---

## Anonymization Mapping (Gap #3 - Documentation)

Ticket IDs in the database are re-keyed (T0001–T0414) for anonymization. Real ticket IDs must be stored in a **secured, separate location** (not in this repository).

**Expected Format** (create when you have real data):
```
# In a secured database or file:
mapping_table:
  T0001 -> REAL_TICKET_ID_1
  T0002 -> REAL_TICKET_ID_2
  ...
  T0414 -> REAL_TICKET_ID_414

# Access pattern:
1. Retrieval returns: {"matches": [{"id": "T0045", ...}]}
2. Look up T0045 in mapping → REAL_ID
3. Display REAL_ID to user (never T0045)
```

**Implementation Notes:**
- Create mapping table with schema: `(anonymized_id TEXT PRIMARY KEY, real_ticket_id TEXT UNIQUE, created_at TIMESTAMPTZ)`
- Maintain in a separate database or secured file
- Update the integration layer (not this module) to translate IDs at the boundary
- Document access controls and audit logging for the mapping

---

## Source Metadata Linking (Gap #5 - Implemented)

The database can link tickets to external evidence (RCA, release notes, runbooks) via `source_metadata` JSONB field.

**Structure:**
```json
{
  "duplicate_of": "T0045",
  "related_source_ids": ["RCA-2024-001", "GW-RN-2025-2-1-004"],
  "linked_chat_rca_ids": ["CHAT-5512"],
  "related_sources": [
    {
      "source_id": "RCA-2024-001",
      "source_type": "rca",
      "title": "Root Cause Analysis: Bind Timeout",
      "text": "Issue was caused by..."
    },
    {
      "source_id": "GW-RN-2025-2-1-004",
      "source_type": "release_note",
      "title": "Release Notes 2025.2.1",
      "text": "Fixed binding timeout in..."
    }
  ]
}
```

**How It's Populated:**
1. Evidence links (RCA, release notes, runbooks) are not carried over since the move to ticket_records; tickets only for now
2. References are looked up in JSON template files:
   - `RCA-*` → `chat_rca.json`
   - `GW-RN-*` → `release_notes.json`
   - `RB-*` → `runbooks.json`
   - `INC-*` / `CHAT-*` → cross-references
3. Linked sources are stored in `related_sources[]` with full text

**Retrieval Integration:**
- LLM can reference source_id in its reasoning (e.g., "per RCA-2024-001: ...")
- Output `reusable_info` field includes source IDs
- Pipeline filters these automatically; not exposed in `matches[]` to users

---

## Development Notes

- **Vector dimension = `EMBEDDING_DIMENSIONS` env (default 3072).** It must equal the DB `vector(N)` column; changing it requires re-embedding.
- **Tests are database-free.** Unit tests mock all external calls; use them for fast iteration before touching the database.
- **Environment values are never logged.** The Settings dataclass does not print credentials.
- **Synthetic test cases stay in tests.py**, never in production ingestion. Add real labelled cases to `evaluation_synthetic_tests.py` when available.
- **The prompt is embedded in `pipeline.py` (SYSTEM_PROMPT).** Updates to classification rules or output format must edit that string and the caller in `pipeline.py`.
- **Temporal boost is configurable but tuned for 365-day window.** Adjust thresholds based on evaluation results (see Gap #2).

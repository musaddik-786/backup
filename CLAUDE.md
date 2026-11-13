# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Knowledge Ops Agent - Knowledge Base for L1-L4 Tier Support**

A runnable implementation of a continuously-refreshed Knowledge Base built from 4 Guidewire sources (historical tickets, chat transcripts & RCAs, release notes, and runbooks). The KB powers L1-L4 tier support agents with unified retrieval across all sources.

**Key principle**: "One Knowledge Base. Four Tier Agents." — the agents query the same unified KB with different filters/ranking strategies (L1 needs fast answers, L4 needs SEV1 precedent for outage mitigation), not separate KBs.

## Architecture: Pipeline Stages

The system implements a 4-stage pipeline, each with its own module:

1. **Ingestion & Normalization** (`ingestion.py`)
   - Loads 4 raw JSON sources from `data/raw/*.json`
   - Validates each record against Pydantic schema (`schemas.py`)
   - Tags every record with taxonomy: `module`, `category`, `severity`, `resolution_type`
   - `module`/`severity`: deterministic from raw fields (rule-based)
   - `category`/`resolution_type`: LLM-classified (one Azure OpenAI call per record, constrained to taxonomy.py enums)
   - Output: list of `NormalizedRecord` objects with `tagging_confidence` (0-1) and `tagging_reasoning`

2. **Structuring Agent** (`structuring.py`)
   - Renders each normalized record as SOP/runbook-style markdown (`structured_sop`)
   - Generates vector embeddings (Azure OpenAI `text-embedding-3-large`, 3072-dim)
   - Batches embedding generation for entire corpus
   - Output: records with `structured_sop` and `embedding` fields populated

3. **SME Curation Gate** (`curation.py`)
   - Records with `tagging_confidence >= 0.72` → auto-approved
   - Below 0.72 → held for human review (simulated; real flow would use a reviewer UI)
   - Enforces `CurationStatus` contract: `AutoApproved` / `SMEApproved` / `SMERejected` / `NeedsRetag`
   - Output: `approved` list and `held_back` list

4. **Knowledge Store** (`knowledge_store.py`, `schema.sql`)
   - PostgreSQL + **pgvector** (vector extension for semantic search)
   - **One table per source** (4 tables total), each with source-specific JSONB metadata:
     - `ticket_records` — priority, assignee_team, created_at, closed_at
     - `chat_rca_records` — record_type, incident_id, participants, root_cause, corrective_actions
     - `release_note_records` — note_type, product, affected_modules
     - `runbook_records` — owner_team, trigger_condition, steps
   - All tables share common "spine": taxonomy tags, `structured_sop`, 3072-dim `vector`, curation status
   - `upsert_records()` refuses to write rows that aren't `AutoApproved` or `SMEApproved` (enforcement, not just policy)
   - Logging: `kb_refresh_log` (every refresh run), `curation_audit_log` (every SME decision across all sources)

**Why four tables, not one?** Each source has its own honest shape (tickets have priority; runbooks have steps). Querying is unified via `UNION ALL` in `search_similar()`, so the agent's query decides what counts as "one KB," not the storage layout. See `query_demo.py` for the four different filter/ranking strategies (L1-L4).

## Data Model

**Raw sources** (`schemas.py`):
- `RawTicket` — ticket_id, module_reported, priority, resolution_notes, assignee_team, etc.
- `RawChatOrRCA` — incident_id, record_type (chat/RCA), participants, root_cause, etc.
- `RawReleaseNote` — release_id, product, version, note_type (feature/resolved_issue/known_issue), affected_modules, etc.
- `RawRunbook` — runbook_id, owner_team, trigger_condition, steps, etc.

**Normalized shape** (all sources converge to `NormalizedRecord`):
- Core: `record_id`, `source_type`, `source_ref_id` (original ticket_id/release_id/etc.)
- Taxonomy: `module`, `category`, `severity`, `resolution_type`, `tagging_confidence` (0-1), `tagging_reasoning`
- Content: `title`, `raw_text`, `event_date`, `guidewire_version`
- After structuring: `structured_sop`, `embedding` (3072-dim list of floats)
- After curation: `curation_status` (enum), `curation_notes`, `kb_version` (when written to store)

**Taxonomy** (`taxonomy.py`):
- `GuidewireModule` — PolicyCenter, ClaimCenter, BillingCenter, ContactManager, Rating, IntegrationGateway, Other
- `IssueCategory` — Bug, FeatureRequest, PerformanceIssue, ConfigurationError, DataIssue, IntegrationIssue, Other
- `Severity` — SEV1, SEV2, SEV3, SEV4
- `ResolutionType` — Patch, ConfigChange, Upgrade, Workaround, Documentation, Unknown
- `CurationStatus` — PENDING_REVIEW, AutoApproved, SMEApproved, SMERejected, NeedsRetag
- `SourceType` — TICKET, CHAT_RCA, RELEASE_NOTE, RUNBOOK

## Common Commands

```bash
# Environment setup
pip install -r requirements.txt

# Database setup (one-time)
python step1_create_database.py       # CREATE DATABASE (Postgres)
python step2_create_tables.py         # CREATE EXTENSION vector; create 4 tables + logging tables

# Full pipeline (ingest, structure, curate, write to KB)
python pipeline.py

# Step-by-step refresh (recommended for first run or validation)
python step3_populate_data.py         # ingest + tag + SOP render, embeddings = NULL
python step4_store_embeddings.py      # find embedding IS NULL, generate, UPDATE

# Demo: query the KB with 4 different L1-L4 tier-agent strategies
python query_demo.py

# Sanity check (one embedding per table, without full refresh)
python test_embedding_storage.py
```

### Key environment variables (.env)

Required:
- `AZURE_OPENAI_API_KEY` — shared with Underwriting Solution
- `AZURE_OPENAI_ENDPOINT` — Azure OpenAI resource URL
- `AZURE_OPENAI_API_VERSION` — API version (2025-01-01-preview or later)
- `AZURE_OPENAI_CHAT_DEPLOYMENT` — deployment for `_llm_classify()` tagging
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME` — deployment for `text-embedding-3-large`
- `PG_HOST`, `PG_PORT`, `PG_DATABASE`, `PG_USER`, `PG_PASSWORD` — Postgres connection
- `PG_SSLMODE` — "require" for managed/cloud Postgres, optional for local

Note: `.env` is in `.gitignore`. Never commit real credentials.

## Key Files & Responsibilities

| File | Purpose |
|------|---------|
| `ingestion.py` | Load raw JSON, validate against Pydantic schemas, LLM-tag category/resolution_type, output `NormalizedRecord` list |
| `structuring.py` | Render records as markdown SOPs, generate 3072-dim embeddings via Azure OpenAI, populate `structured_sop` and `embedding` fields |
| `curation.py` | Apply confidence threshold (0.72), split into `approved` and `held_back` lists, annotate with `CurationStatus` |
| `knowledge_store.py` | Postgres + pgvector interface: `get_connection()`, `init_schema()`, `upsert_records()`, `search_similar()`, audit logging |
| `pipeline.py` | Orchestrate stages 1-4, handle errors gracefully (KB write is optional if Postgres unavailable) |
| `query_demo.py` | Demonstrate four different retrieval strategies (L1-L4 filters, ranking, top_k) against the unified KB |
| `schemas.py` | Pydantic models for raw sources and normalized records |
| `taxonomy.py` | Enum definitions: modules, categories, severity, resolution types, curation status, source types |
| `schema.sql` | DDL for all tables: 4 source tables, refresh_log, curation_audit_log, pgvector setup |
| `step1_create_database.py` | CREATE DATABASE (Postgres-specific) |
| `step2_create_tables.py` | CREATE EXTENSION vector; run schema.sql |
| `step3_populate_data.py` | Load data/raw/*.json, ingest/tag/structure/curate, log stats (no embeddings yet) |
| `step4_store_embeddings.py` | Find rows with embedding IS NULL, generate embeddings, UPDATE (safe to re-run) |
| `test_embedding_storage.py` | Sanity check: one embedding per table, end-to-end (includes DB write) |
| `data/raw/*.json` | Mock data for 4 sources (31 hand-built records, internally consistent) |
| `example_trace.py` | Standalone example showing the raw→normalized→structured pipeline |

## Important Design Decisions

1. **Confidence-driven curation**: Records carry `tagging_confidence` and `tagging_reasoning` so humans can review the *why*, not just the *what*. The curation gate enforces a 0.72 threshold in code.

2. **Four tables, not one**: Each source keeps its honest shape (tickets have priority fields; runbooks have steps). Queries unify via `UNION ALL`, so the *retrieval logic*, not the schema, decides what "one KB" means.

3. **Embeddings without indexes**: 3072-dim exceeds pgvector's 2000-dim index limit, so searches use sequential scan (`ORDER BY embedding <=> query`). Same approach as Underwriting Solution. Acceptable at current scale; revisit (halfvec or smaller dimension) if row counts grow into tens of thousands.

4. **Ingestion graceful degradation**: If Postgres connection fails, stages 1-3 still run and report results. If Azure OpenAI is unreachable, a clear `RuntimeError` names the missing var(s) rather than failing deep in the SDK.

5. **SME review contract**: The `CurationStatus` enum is what a real reviewer UI would write to. Simulation here uses deterministic confidence-based auto-approval; real workflow reads/writes the same enum values.

## Running the Pipeline

**Without Postgres** (validation only):
```bash
python pipeline.py  # Stages 1-3 complete, stage 4 reports "SKIPPED" with friendly message
```

**With Postgres + pgvector**:
1. Set up `.env` with Postgres and Azure OpenAI credentials.
2. Run `step1_create_database.py` once.
3. Run `step2_create_tables.py` once.
4. Re-run steps 3-4 anytime for a refresh (safe; step 4 skips rows that already have embeddings).
5. Or: `python pipeline.py` for one-shot ingestion+structure+curate+write.

**Verify the KB is working**:
```bash
python query_demo.py  # Runs 4 different agent queries (L1-L4) against the same KB
```

## Testing & Validation

- `test_embedding_storage.py` — sanity check after DB setup: one embedding per table, all the way through to Postgres.
- `example_trace.py` — standalone example showing raw→normalized→structured pipeline without DB/Azure dependencies.
- `query_demo.py` — integration test: demonstrates retrieval with real data and 4 different filtering strategies.

No pytest suite yet. Pipeline is validated by running `python pipeline.py` and checking the console output.

## Notes on Simplifications

- **Tagging**: Module/severity are rule-based from raw fields. Category/resolution_type use LLM (one Azure OpenAI call per record, constrained to taxonomy enums), so a human can see the reasoning.
- **Embeddings**: 3072-dim is too large for pgvector's built-in ivfflat/hnsw indexes. We use sequential scan; revisit if data grows.
- **SME review**: Simulated via deterministic confidence threshold. Real UI would let reviewers edit tags and override status; the enum contract is unchanged.
- **Mock data**: 31 hand-built records across PolicyCenter, ClaimCenter, BillingCenter, ContactManager, Rating, IntegrationGateway. Replace with real exports when available.

None of these affect the schema, storage design, or "one KB, four agents" query pattern — the core proof-of-concept.

## Git Workflow

- Current branch: `feature/Musaddique/gw-release-notes-kb` (Knowledge Base for Guidewire release notes and KB).
- Main branch: `main`
- Commits should reference the stage/file changed (e.g., "Refine ingestion confidence thresholding in curation.py").

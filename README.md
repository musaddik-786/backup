# Knowledge Base for Knowledge Ops Agent

A working, runnable implementation of the architecture diagram: a single,
continuously-refreshed Knowledge Base built from 4 sources, that will go
on to power the L1-L4 tier agents (and later, MCP tools/servers on top of
it -- that layer is intentionally **not** built yet; this phase is the KB
itself).

## Architecture -> code map

| Diagram box | File |
|---|---|
| 4 raw sources (Tickets, Chat/RCA, Release Notes, Runbooks) | `data/raw/*.json`, schemas in `schemas.py` |
| Ingestion & Normalization | `ingestion.py` |
| Structuring Agent | `structuring.py` |
| SME Curation Gate | `curation.py` |
| Knowledge Store | `knowledge_store.py` + `schema.sql` (PostgreSQL + pgvector, 4 tables -- one per source) |
| Taxonomy (module / category / severity / resolution type) | `taxonomy.py` |
| Full refresh pipeline | `pipeline.py` |
| "One KB, four tier agents" demo | `query_demo.py` |

## Pipeline, stage by stage

1. **Ingestion & Normalization** (`ingestion.py`)
   Loads the 4 raw sources (each validated against its own Pydantic
   schema, so a malformed record fails loudly instead of corrupting the
   KB silently), and tags every record against the taxonomy: `module`,
   `category`, `severity`, `resolution_type`. Each tag gets a
   `tagging_confidence` score (0-1).

   `module` and `severity` are resolved deterministically from fields
   already in the raw source (a ticket's `module_reported`/`priority`,
   a linked incident's severity, etc.), with a small rule-based fallback
   only where the raw data doesn't give a direct answer.

   `category` and `resolution_type` are tagged by **one Azure OpenAI
   chat-model call per record** (`_llm_classify()`): the model is given
   the record's narrative text plus the fixed list of valid
   `IssueCategory`/`ResolutionType` values from `taxonomy.py`, and
   returns a structured pick for each, a self-reported confidence
   (0-1), and a short `tagging_reasoning` string -- so a human reviewer
   can still see *why* a tag was chosen, same transparency goal as the
   original keyword classifier, now backed by a real model instead of
   hand-written keyword lists.

2. **Structuring Agent** (`structuring.py`)
   Renders every normalized record into the same SOP/runbook-style
   markdown block (so a raw ticket and a formal RCA report look
   structurally identical), and generates a vector embedding for each
   record so it can be retrieved by meaning, not just keywords.

   Embeddings use **Azure OpenAI `text-embedding-3-large`** (3072-dim),
   via the same `langchain_openai.AzureOpenAIEmbeddings` client and Azure
   OpenAI resource as the Underwriting Solution project. `fit_embeddings()`
   batches the embed call for the whole corpus; `embed_query()` embeds a
   single free-text query the same way, so both land in the same vector
   space and are directly comparable via cosine similarity.

3. **SME Curation Gate** (`curation.py`)
   Records with `tagging_confidence >= 0.72` are auto-approved. Below
   that, a human SME reviews before anything goes live (simulated here
   so the pipeline runs end-to-end without a review UI -- the interface
   is exactly what a real reviewer workflow would read/write). Records
   that fail review are held back and logged, never silently dropped.

4. **Knowledge Store** (`knowledge_store.py`, `schema.sql`)
   PostgreSQL + **pgvector**. **One table per source**, each with its own
   `vector(3072)` embedding column and a JSONB `source_metadata` column
   for whatever extra fields only make sense for that source:

   | Table | Source | Extra fields in `source_metadata` |
   |---|---|---|
   | `ticket_records` | Historical Tickets | priority, assignee_team, created_at, closed_at |
   | `chat_rca_records` | Chat & RCA Reports | record_type, incident_id, participants, root_cause, corrective_actions |
   | `release_note_records` | Guidewire Release Notes | note_type, product, affected_modules |
   | `runbook_records` | Runbooks/SOPs | owner_team, trigger_condition, steps |

   All 4 tables share the same "spine" of columns (taxonomy tags,
   `structured_sop`, `embedding`, curation status), which is what makes
   it possible to query across them uniformly.

   `upsert_records()` routes each record into its source's table and
   **refuses** to write any record that isn't `AutoApproved`/`SMEApproved`
   -- the curation gate is enforced in code, not just on paper.
   Every refresh run is logged in `kb_refresh_log`; every SME decision is
   logged in `curation_audit_log` (shared across all 4 tables).

## Why four tables, one knowledge base

The diagram's whole point ("One Knowledge Base. Four Tier Agents.") is
that L1-L4 don't get separate *knowledge bases* -- but that doesn't
require cramming every source into one physical table. Splitting by
source keeps each table's shape honest (a ticket really does have a
priority field; a runbook really does have steps) while still letting
any query span all 4 at once via `UNION ALL` -- see
`knowledge_store.search_similar()`, which takes a `source_type_in` list
and unions only the tables that list names (all 4 by default). The
*agent's query*, not the storage layout, decides whether it's "one
knowledge base" for that request. `query_demo.py` shows this
concretely: same 4 tables, four different filter/ranking strategies:

- **L1 Guided Resolution** -- tight module+category filter, proven
  fixes only (tickets/runbooks), small `top_k`, needs an answer fast.
- **L2 Resolution Intelligence** -- same module, wider net, higher
  `top_k`, looking for "similar past issues."
- **L3 Problem Ops & RCA** -- filtered to RCA reports + release notes
  only (the "why", not the "how"), SEV1/SEV2 only, investigating
  systemic patterns.
- **L4 MIM Coordination** -- SEV1 precedent only, no category filter,
  because a live outage can't wait for perfect categorization.

## Pre-setup checklist

1. **Python 3.11+** with `pip` available.
2. **A PostgreSQL instance** you can connect to (local install, Docker, or
   a hosted free tier like Supabase/Neon). On Windows with the EDB
   installer, this is whatever you set up via the installer/Stack Builder.
3. **The `pgvector` extension installed on that Postgres instance** --
   not just the Python `pgvector` package. Verify with:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
   run against your target database. If this errors, pgvector isn't
   installed on the Postgres server itself yet (see troubleshooting below).
4. **A database created** for this project, e.g.:
   ```sql
   CREATE DATABASE knowledge_ops_kb;
   ```
5. **Python dependencies installed**:
   ```bash
   pip install -r requirements.txt
   ```
6. **A `.env` file** in the project root (see below) -- `knowledge_store.py`
   loads it automatically via `python-dotenv`, so you don't need to
   `export` the connection string manually in every new terminal.
7. **Azure OpenAI access** -- the same Azure OpenAI resource/deployment
   your Underwriting Solution project uses for embeddings
   (`text-embedding-3-large`). Needed for stage 2 (`structuring.py`) to
   generate embeddings at all, independent of whether Postgres is reachable.

## `.env` file

Copy `.env.example` to `.env` and fill in your real connection details:

```bash
cp .env.example .env
```

Connection details are individual components, not one combined URL
(avoids any password-special-character/URL-encoding issues, and matches
this team's existing `.env` convention for Postgres):

```bash
PG_HOST="localhost"
PG_PORT="5432"
PG_DATABASE="knowledgeops_gw"
PG_USER="postgres"
PG_PASSWORD="your_password_here"
# PG_SSLMODE="require"   # optional, for managed/cloud Postgres that enforces SSL

AZURE_OPENAI_API_KEY="..."
AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
AZURE_OPENAI_API_VERSION="2025-01-01-preview"
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME="..."
AZURE_OPENAI_CHAT_DEPLOYMENT="..."
```
Reuse the exact same Azure OpenAI values your Underwriting Solution
project already has -- same resource, same deployments (embedding
deployment for `structuring.py`, chat deployment for `ingestion.py`'s
LLM tagging).

`.env` is already in `.gitignore` -- never commit it with real
credentials in it.

## Running it

### Option A -- one shot, everything at once
```bash
python pipeline.py        # ingest, structure+embed, SME-gate, write to Postgres
python query_demo.py      # demo: same KB, 4 different tier-agent query strategies
```

### Option B -- step by step (recommended the first time, so you can see
### each piece land before moving to the next)
```bash
python step1_create_database.py      # CREATE DATABASE (if it doesn't exist)
python step2_create_tables.py        # CREATE EXTENSION vector; creates all 4 tables, empty
python step3_populate_data.py        # ingest+tag+SOP+curate -> INSERT rows, embedding = NULL
python step4_store_embeddings.py     # find rows with embedding IS NULL -> generate -> UPDATE
python query_demo.py                 # demo: same KB, 4 different tier-agent query strategies
```
Steps 1-2 only need to be run once. Steps 3-4 are the actual "refresh" --
re-running step 3 updates tags/SOP text on existing rows without
touching their embeddings; step 4 then only generates embeddings for
whatever is still NULL (new rows), so it's safe to re-run both anytime.

```bash
# Focused check: generate + store + read back ONE embedding per table in
# one go (good sanity check after DB setup, outside the step-by-step flow)
python test_embedding_storage.py
```

Without `PG_*` vars set, `pipeline.py` and `test_embedding_storage.py`
still run ingestion/structuring/curation and report results, then exit
cleanly instead of failing on the DB step -- useful for validating the
tagging logic without a database handy. Embedding generation itself,
however, does need the `AZURE_OPENAI_*` vars set (it's a real API call,
not a local model) -- without those, stage 2 raises a clear
`RuntimeError` naming the missing var(s) rather than failing deep inside
the Azure SDK.

## Troubleshooting pgvector on Windows

If `CREATE EXTENSION vector;` fails with something like
`extension "vector" is not available`, the extension isn't installed on
the Postgres server yet (this is separate from `pip install pgvector`,
which only installs the *Python client library*). Options, easiest first:

- **Stack Builder** (bundled with the EDB Windows installer): Start menu
  -> Stack Builder -> select your Postgres instance -> look under
  "Spatial Extensions" / "Add-ons" for pgvector.
- **Prebuilt binaries**: search for a prebuilt pgvector release matching
  your exact Postgres major version and Windows architecture, and drop
  the files into your Postgres `lib`/`share/extension` folders per its
  README.
- **Build from source**: requires Visual Studio Build Tools (C compiler)
  and the Postgres dev headers; follow the build steps in the
  [pgvector repo](https://github.com/pgvector/pgvector) README.

If you get stuck here, tell me the exact error and how Postgres was
installed (EDB installer vs. something else) and we'll troubleshoot it
directly.

## Mock data

Since this phase has no real tickets wired in yet, `data/raw/*.json`
contains 31 hand-built, internally-consistent sample records across
PolicyCenter, ClaimCenter, BillingCenter, ContactManager, Rating/Gosu and
IntegrationGateway -- including a full incident chain (ticket -> war-room
chat -> formal RCA -> vendor release-note fix) so the retrieval demo has
something real to find. Swap in actual exports once available; the
schemas in `schemas.py` (`RawTicket`, `RawChatOrRCA`, `RawReleaseNote`,
`RawRunbook`) define exactly what shape real data needs to arrive in.

## Known simplifications (flagged, not hidden)

- **Tagging classifier**: `module`/`severity` are rule-based (resolved
  from raw fields); `category`/`resolution_type` are LLM-tagged (one
  Azure OpenAI chat call per record, constrained to the taxonomy.py
  enums, with a self-reported confidence + reasoning string). The
  confidence-score + `tagging_reasoning` contract is what the SME
  Curation Gate and a human reviewer both depend on -- that's unchanged
  regardless of which approach drives the tagging.
- **Embeddings** use Azure OpenAI `text-embedding-3-large` (3072-dim),
  the same model/resource as Underwriting Solution. Because 3072 exceeds
  pgvector's 2000-dim index limit, these columns have **no** ivfflat/hnsw
  index -- search is a plain sequential scan (`ORDER BY embedding <=>
  query`), same as Underwriting Solution's `query_retriever_pgvector.py`.
  Fine at this KB's current scale; revisit (halfvec, or a smaller
  embedding dimension) if row counts grow into the tens of thousands.
- **SME review** is simulated, not a real reviewer UI. The
  `CurationStatus` contract (`AutoApproved` / `SMEApproved` /
  `SMERejected` / `NeedsRetag`) is what a real review workflow would
  read and write.

None of these affect the schema, the Postgres/pgvector storage design,
or the "one KB, four agents" query pattern -- which is the part this
phase is meant to prove out.

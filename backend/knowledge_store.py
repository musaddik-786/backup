# """
# Stage 4: Knowledge Store (PostgreSQL + pgvector)
# (diagram box: "A combined vector + structured knowledge store (SOPs,
# runbooks, KB articles) -- refreshed continuously as tickets close and
# re-synced on every Guidewire release cycle.")

# Table layout: ONE TABLE PER SOURCE, each with its own vector(1536)
# embedding column:
#     ticket_records        <- HistoricalTicket
#     chat_rca_records      <- ChatOrRCAReport
#     release_note_records  <- GuidewireReleaseNote
#     runbook_records       <- RunbookOrSOP

# Each table shares the same "spine" of columns (taxonomy tags,
# structured_sop, embedding, curation status) plus a JSONB
# `source_metadata` column holding whatever extra fields only make sense
# for that source (a ticket's priority/assignee_team, a runbook's
# owner_team/steps, etc).

# Retrieval across the "one KB, four tier agents" pattern is done with a
# UNION ALL across whichever tables a given query cares about -- see
# search_similar(). An agent that only wants ticket+runbook history (L1)
# unions 2 tables; an agent that wants everything (L2) unions all 4.

# Connection:
#   Reads individual connection components from environment variables
#   (via .env), rather than one combined URL -- avoids any password
#   special-character/URL-encoding issues and matches this team's existing
#   .env convention (see PG_HOST / PG_PORT / PG_DATABASE / PG_USER /
#   PG_PASSWORD / PG_SSLMODE below).

# Guardrail:
#   upsert_records() only accepts records whose curation_status is
#   AutoApproved or SMEApproved -- it refuses to write anything the SME
#   Curation Gate didn't clear.
# """

# from __future__ import annotations

# import json
# import os
# import uuid
# from datetime import datetime, timezone

# import psycopg
# from dotenv import load_dotenv
# from pgvector import Vector
# from pgvector.psycopg import register_vector

# from schemas import NormalizedRecord
# from taxonomy import CurationStatus, SourceType

# load_dotenv()  # reads .env in the project root, if present, into os.environ

# ELIGIBLE_FOR_STORE = {CurationStatus.AUTO_APPROVED, CurationStatus.SME_APPROVED}

# # Maps each source type to the table that holds it.
# TABLE_BY_SOURCE_TYPE: dict[SourceType, str] = {
#     SourceType.TICKET: "ticket_records",
#     SourceType.CHAT_RCA: "chat_rca_records",
#     SourceType.RELEASE_NOTE: "release_note_records",
#     SourceType.RUNBOOK: "runbook_records",
# }
# ALL_TABLES = list(TABLE_BY_SOURCE_TYPE.values())


# def _pg_connect_kwargs(dbname: str | None = None) -> dict:
#     """Builds psycopg.connect()'s keyword args from individual PG_* env
#     vars (read from .env). Passing kwargs directly -- rather than
#     building a postgresql://... URL string -- sidesteps any issue with
#     special characters (@, :, /, #, etc.) in PG_PASSWORD needing
#     URL-encoding."""
#     host = os.environ.get("PG_HOST")
#     port = os.environ.get("PG_PORT", "5432")
#     user = os.environ.get("PG_USER")
#     password = os.environ.get("PG_PASSWORD")
#     database = dbname or os.environ.get("PG_DATABASE")
#     sslmode = os.environ.get("PG_SSLMODE")  # optional, e.g. "require"

#     missing = [
#         name for name, val in [("PG_HOST", host), ("PG_USER", user),
#                                 ("PG_PASSWORD", password), ("PG_DATABASE", database)]
#         if not val
#     ]
#     if missing:
#         raise RuntimeError(
#             f"Missing required .env variable(s): {', '.join(missing)}.\n"
#             "Copy .env.example to .env and fill in PG_HOST / PG_PORT / PG_DATABASE / "
#             "PG_USER / PG_PASSWORD (PG_SSLMODE is optional)."
#         )

#     kwargs = {
#         "host": host,
#         "port": int(port),
#         "user": user,
#         "password": password,
#         "dbname": database,
#     }
#     if sslmode:
#         kwargs["sslmode"] = sslmode
#     return kwargs


# def get_connection() -> psycopg.Connection:
#     conn = psycopg.connect(**_pg_connect_kwargs(), autocommit=False)
#     # Registers the Vector <-> pgvector(n) adapter on this connection, so
#     # Vector(...)-wrapped lists can be passed directly as query params and
#     # read back as vectors. On a BRAND NEW database (before step2's
#     # `CREATE EXTENSION vector` has ever run), the "vector" type doesn't
#     # exist yet, so this raises -- that's fine here specifically, since
#     # the very next thing that happens on a fresh DB is init_schema(),
#     # which creates the extension and re-registers it itself. Any other
#     # script calling get_connection() is assumed to run against a DB
#     # that already has the extension (i.e. step2 has already been run).
#     try:
#         register_vector(conn)
#     except psycopg.ProgrammingError:
#         conn.rollback()
#     return conn


# def init_schema(conn: psycopg.Connection) -> None:
#     from pathlib import Path

#     sql = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text(encoding="utf-8")
#     with conn.cursor() as cur:
#         cur.execute(sql)
#     conn.commit()
#     # The vector type only exists after schema.sql runs, so the adapter
#     # must be (re)registered on this connection now that it's live.
#     register_vector(conn)


# def create_database_if_not_exists() -> str:
#     """Step 1 of the manual workflow: CREATE DATABASE for PG_DATABASE (from
#     .env), if it doesn't already exist.

#     CREATE DATABASE cannot run inside a transaction and cannot run on
#     the connection you're about to use the new database from -- so this
#     connects to Postgres's own always-present "postgres" maintenance
#     database (same host/user/password/port, just a different dbname),
#     in autocommit mode, to issue the CREATE DATABASE statement. Returns
#     the target database name.
#     """
#     target_db = os.environ.get("PG_DATABASE")
#     if not target_db:
#         raise RuntimeError(
#             "PG_DATABASE is not set. Copy .env.example to .env and fill in "
#             "PG_HOST / PG_PORT / PG_DATABASE / PG_USER / PG_PASSWORD."
#         )

#     conn = psycopg.connect(**_pg_connect_kwargs(dbname="postgres"), autocommit=True)
#     try:
#         with conn.cursor() as cur:
#             cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
#             exists = cur.fetchone() is not None
#             if exists:
#                 print(f"Database '{target_db}' already exists -- nothing to do.")
#             else:
#                 # Database names can't be parameterized in CREATE DATABASE;
#                 # psycopg's sql.Identifier safely quotes it instead of
#                 # string-formatting it in directly.
#                 from psycopg import sql

#                 cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
#                 print(f"Database '{target_db}' created.")
#     finally:
#         conn.close()

#     return target_db


# def insert_records_no_embedding(
#     conn: psycopg.Connection,
#     records: list[NormalizedRecord],
#     kb_version: str,
# ) -> dict[str, int]:
#     """Writes freshly-ingested/tagged/structured records into their tables
#     WITHOUT an embedding (embedding column is left NULL on insert) --
#     regardless of curation_status. Every record coming out of the real
#     pipeline is PENDING_REVIEW (see curation.py), so this is what actually
#     makes a record visible to a human reviewer: it sits in its table with
#     embedding=NULL and curation_status='PendingReview' until sme_review.py
#     approves/rejects/retags it. search_similar() already excludes anything
#     that isn't AutoApproved/SMEApproved, so a pending row is never
#     retrievable in the meantime.

#     On conflict (re-running against an already-populated row), this
#     deliberately does NOT touch the embedding or curation_status columns
#     -- so re-ingesting a record that's already sitting in the review
#     queue (or already reviewed) never wipes out a human's decision or an
#     embedding sme_review.py already generated. Only use this for records
#     that genuinely need (re)tagging -- ingest_all()'s existing_hashes skip
#     already keeps already-decided, unchanged records out of this call.
#     """
#     written: dict[str, int] = {t: 0 for t in ALL_TABLES}
#     with conn.cursor() as cur:
#         for r in records:
#             table = TABLE_BY_SOURCE_TYPE[r.source_type]
#             cur.execute(
#                 f"""
#                 INSERT INTO {table} (
#                     record_id, source_ref_id, module, category, severity,
#                     resolution_type, tagging_confidence, tagging_reasoning, title, raw_text, structured_sop,
#                     guidewire_version, event_date, source_url, source_metadata,
#                     curation_status, curation_notes, kb_version, ingested_at, content_hash
#                 ) VALUES (
#                     %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
#                 )
#                 ON CONFLICT (record_id) DO UPDATE SET
#                     source_ref_id = EXCLUDED.source_ref_id,
#                     module = EXCLUDED.module,
#                     category = EXCLUDED.category,
#                     severity = EXCLUDED.severity,
#                     resolution_type = EXCLUDED.resolution_type,
#                     tagging_confidence = EXCLUDED.tagging_confidence,
#                     tagging_reasoning = EXCLUDED.tagging_reasoning,
#                     title = EXCLUDED.title,
#                     raw_text = EXCLUDED.raw_text,
#                     structured_sop = EXCLUDED.structured_sop,
#                     guidewire_version = EXCLUDED.guidewire_version,
#                     event_date = EXCLUDED.event_date,
#                     source_url = EXCLUDED.source_url,
#                     source_metadata = EXCLUDED.source_metadata,
#                     kb_version = EXCLUDED.kb_version,
#                     ingested_at = EXCLUDED.ingested_at,
#                     content_hash = EXCLUDED.content_hash,
#                     upserted_at = now();
#                     -- embedding and curation_status intentionally NOT in
#                     -- this SET list -- see docstring above
#                 """,
#                 (
#                     r.record_id,
#                     r.source_ref_id,
#                     r.module.value,
#                     r.category.value,
#                     r.severity.value,
#                     r.resolution_type.value if r.resolution_type else None,
#                     r.tagging_confidence,
#                     r.tagging_reasoning,
#                     r.title,
#                     r.raw_text,
#                     r.structured_sop,
#                     r.guidewire_version,
#                     r.event_date,
#                     r.source_url,
#                     json.dumps(r.source_metadata) if r.source_metadata is not None else None,
#                     r.curation_status.value,
#                     r.curation_notes,
#                     kb_version,
#                     r.ingested_at,
#                     r.content_hash,
#                 ),
#             )
#             written[table] += 1
#     conn.commit()
#     return written


# def fetch_rows_missing_embedding(conn: psycopg.Connection) -> dict[str, list[tuple[str, str, str]]]:
#     """Step 4 of the manual workflow, part 1: find every row across all
#     4 tables that's cleared for embedding -- i.e. AutoApproved/SMEApproved
#     AND still has embedding IS NULL. A PendingReview/SMERejected/
#     NeedsRetag row is deliberately excluded: nothing gets embedded before
#     a human has approved it. Returns {table_name: [(record_id, title, raw_text), ...]}."""
#     pending: dict[str, list[tuple[str, str, str]]] = {}
#     with conn.cursor() as cur:
#         for table in ALL_TABLES:
#             cur.execute(
#                 f"""SELECT record_id, title, raw_text FROM {table}
#                     WHERE embedding IS NULL
#                       AND curation_status IN ('AutoApproved', 'SMEApproved')"""
#             )
#             pending[table] = cur.fetchall()
#     return pending


# def fetch_pending_review(conn: psycopg.Connection) -> list[dict]:
#     """Returns every row across all 4 tables still awaiting a human
#     decision (curation_status='PendingReview'), for sme_review.py to show
#     a reviewer. Each dict includes which table it came from, since the
#     reviewer's decision has to be written back to that same table."""
#     rows: list[dict] = []
#     with conn.cursor() as cur:
#         for table in ALL_TABLES:
#             cur.execute(
#                 f"""SELECT record_id, source_ref_id, module, category, severity,
#                            resolution_type, tagging_confidence, tagging_reasoning,
#                            title, raw_text, structured_sop, kb_version
#                     FROM {table}
#                     WHERE curation_status = 'PendingReview'
#                     ORDER BY ingested_at"""
#             )
#             cols = [desc[0] for desc in cur.description]
#             for row in cur.fetchall():
#                 record = dict(zip(cols, row))
#                 record["table"] = table
#                 rows.append(record)
#     return rows


# def set_curation_decision(
#     conn: psycopg.Connection,
#     table: str,
#     record_id: str,
#     status: CurationStatus,
#     notes: str,
# ) -> None:
#     """Writes a human reviewer's decision (sme_review.py) back to the one
#     row it concerns. Does NOT touch embedding -- approving a record and
#     generating its embedding are deliberately two separate calls (see
#     sme_review.py), so a caller can never accidentally mark something
#     SMEApproved without actually having embedded it right after."""
#     with conn.cursor() as cur:
#         cur.execute(
#             f"UPDATE {table} SET curation_status = %s, curation_notes = %s WHERE record_id = %s",
#             (status.value, notes, record_id),
#         )
#     conn.commit()


# def register_approved_hash(conn: psycopg.Connection, table: str, record_id: str, kb_version: str) -> None:
#     """After sme_review.py approves a record, records its content_hash
#     (already stored on the row itself -- see insert_records_no_embedding)
#     into ingestion_state, so the next pipeline run's duplicate-run
#     prevention skips re-tagging it. Mirrors upsert_ingestion_hashes(), but
#     reads the hash back from the row instead of requiring the in-memory
#     NormalizedRecord (which no longer exists by the time a human reviews
#     a record written in an earlier run)."""
#     with conn.cursor() as cur:
#         cur.execute(f"SELECT content_hash FROM {table} WHERE record_id = %s", (record_id,))
#         row = cur.fetchone()
#         if row is None or row[0] is None:
#             return
#         content_hash = row[0]
#         source_type = next(st.value for st, t in TABLE_BY_SOURCE_TYPE.items() if t == table)
#         cur.execute(
#             """INSERT INTO ingestion_state (record_id, source_type, content_hash, kb_version)
#                VALUES (%s, %s, %s, %s)
#                ON CONFLICT (record_id) DO UPDATE SET
#                    source_type = EXCLUDED.source_type,
#                    content_hash = EXCLUDED.content_hash,
#                    last_ingested_at = now(),
#                    kb_version = EXCLUDED.kb_version""",
#             (record_id, source_type, content_hash, kb_version),
#         )
#     conn.commit()


# def update_embedding(conn: psycopg.Connection, table: str, record_id: str, embedding: list[float]) -> None:
#     """Step 4 of the manual workflow, part 2: write a generated embedding
#     into one specific row's embedding column."""
#     with conn.cursor() as cur:
#         cur.execute(
#             f"UPDATE {table} SET embedding = %s WHERE record_id = %s",
#             (Vector(embedding), record_id),
#         )


# def new_kb_version() -> str:
#     """One version id per refresh run -- written to kb_refresh_log and
#     stamped on every record upserted in that run, so you can always
#     answer 'what changed in the last sync'."""
#     return f"kb-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


# def _upsert_sql_for(table: str) -> str:
#     return f"""
#         INSERT INTO {table} (
#             record_id, source_ref_id, module, category, severity,
#             resolution_type, tagging_confidence, tagging_reasoning, title, raw_text, structured_sop,
#             guidewire_version, event_date, embedding, source_url, source_metadata,
#             curation_status, curation_notes, kb_version, ingested_at
#         ) VALUES (
#             %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
#         )
#         ON CONFLICT (record_id) DO UPDATE SET
#             source_ref_id = EXCLUDED.source_ref_id,
#             module = EXCLUDED.module,
#             category = EXCLUDED.category,
#             severity = EXCLUDED.severity,
#             resolution_type = EXCLUDED.resolution_type,
#             tagging_confidence = EXCLUDED.tagging_confidence,
#             tagging_reasoning = EXCLUDED.tagging_reasoning,
#             title = EXCLUDED.title,
#             raw_text = EXCLUDED.raw_text,
#             structured_sop = EXCLUDED.structured_sop,
#             guidewire_version = EXCLUDED.guidewire_version,
#             event_date = EXCLUDED.event_date,
#             embedding = EXCLUDED.embedding,
#             source_url = EXCLUDED.source_url,
#             source_metadata = EXCLUDED.source_metadata,
#             curation_status = EXCLUDED.curation_status,
#             curation_notes = EXCLUDED.curation_notes,
#             kb_version = EXCLUDED.kb_version,
#             ingested_at = EXCLUDED.ingested_at,
#             upserted_at = now();
#     """


# def upsert_records(
#     conn: psycopg.Connection,
#     records: list[NormalizedRecord],
#     kb_version: str,
# ) -> dict[str, int]:
#     """Insert or refresh records, routing each one into its source's own
#     table. Rejects any record the SME Curation Gate has not cleared.
#     Returns a dict of {table_name: rows_written}."""
#     rejected = [r for r in records if r.curation_status not in ELIGIBLE_FOR_STORE]
#     if rejected:
#         ids = ", ".join(r.record_id for r in rejected)
#         raise ValueError(
#             f"Refusing to upsert {len(rejected)} record(s) that are not "
#             f"AutoApproved/SMEApproved: {ids}"
#         )

#     written: dict[str, int] = {t: 0 for t in ALL_TABLES}
#     with conn.cursor() as cur:
#         for r in records:
#             table = TABLE_BY_SOURCE_TYPE[r.source_type]
#             cur.execute(
#                 _upsert_sql_for(table),
#                 (
#                     r.record_id,
#                     r.source_ref_id,
#                     r.module.value,
#                     r.category.value,
#                     r.severity.value,
#                     r.resolution_type.value if r.resolution_type else None,
#                     r.tagging_confidence,
#                     r.tagging_reasoning,
#                     r.title,
#                     r.raw_text,
#                     r.structured_sop,
#                     r.guidewire_version,
#                     r.event_date,
#                     Vector(r.embedding),
#                     r.source_url,
#                     json.dumps(r.source_metadata) if r.source_metadata is not None else None,
#                     r.curation_status.value,
#                     r.curation_notes,
#                     kb_version,
#                     r.ingested_at,
#                 ),
#             )
#             written[table] += 1
#     conn.commit()
#     return written


# def fetch_ingestion_hashes(conn: psycopg.Connection) -> dict[str, str]:
#     """Returns {record_id: content_hash} for every record a prior run fully
#     processed (tagged + approved), so ingestion.ingest_all() can skip
#     re-running the LLM tagging call for anything whose raw source fields
#     haven't changed since then. Returns {} on a brand-new database (table
#     doesn't exist yet) instead of raising, so callers can use this before
#     init_schema() has necessarily run."""
#     with conn.cursor() as cur:
#         try:
#             cur.execute("SELECT record_id, content_hash FROM ingestion_state")
#             return dict(cur.fetchall())
#         except psycopg.errors.UndefinedTable:
#             conn.rollback()
#             return {}


# def upsert_ingestion_hashes(
#     conn: psycopg.Connection,
#     records: list[NormalizedRecord],
#     kb_version: str,
# ) -> None:
#     """Records the content hash of every record actually (re)processed this
#     run, so the next run can detect which ones are unchanged and skip them.
#     Only call this with AutoApproved/SMEApproved records -- a record that
#     came back NeedsRetag is deliberately left out of `records` by the
#     caller, so it keeps getting retried on every run instead of being
#     silently skipped forever."""
#     rows = [
#         (r.record_id, r.source_type.value, r.content_hash, kb_version)
#         for r in records
#         if r.content_hash is not None
#     ]
#     if not rows:
#         return
#     with conn.cursor() as cur:
#         cur.executemany(
#             """INSERT INTO ingestion_state (record_id, source_type, content_hash, kb_version)
#                VALUES (%s, %s, %s, %s)
#                ON CONFLICT (record_id) DO UPDATE SET
#                    source_type = EXCLUDED.source_type,
#                    content_hash = EXCLUDED.content_hash,
#                    last_ingested_at = now(),
#                    kb_version = EXCLUDED.kb_version""",
#             rows,
#         )
#     conn.commit()


# def log_curation_audit(conn: psycopg.Connection, records: list[NormalizedRecord]) -> None:
#     rows = [
#         (r.record_id, r.source_type.value, r.tagging_confidence, r.curation_status.value, r.curation_notes)
#         for r in records
#     ]
#     with conn.cursor() as cur:
#         cur.executemany(
#             """INSERT INTO curation_audit_log
#                (record_id, source_type, tagging_confidence, curation_status, curation_notes)
#                VALUES (%s, %s, %s, %s, %s)""",
#             rows,
#         )
#     conn.commit()


# def log_refresh_run(
#     conn: psycopg.Connection,
#     kb_version: str,
#     ingested: int,
#     approved: int,
#     held_back: int,
# ) -> None:
#     with conn.cursor() as cur:
#         cur.execute(
#             """INSERT INTO kb_refresh_log
#                (kb_version, records_ingested, records_approved, records_held_back)
#                VALUES (%s, %s, %s, %s)""",
#             (kb_version, ingested, approved, held_back),
#         )
#     conn.commit()


# def search_similar(
#     conn: psycopg.Connection,
#     query_embedding: list[float],
#     top_k: int = 5,
#     module: str | None = None,
#     category: str | None = None,
#     severity_in: list[str] | None = None,
#     source_type_in: list[str] | None = None,
# ) -> list[dict]:
#     """
#     Core retrieval function every tier agent (L1-L4) calls, with a
#     different filter combination per agent -- see query_demo.py.

#     source_type_in picks WHICH of the 4 tables to search (defaults to all
#     4); module/category/severity_in filter rows within each table. All
#     selected tables are combined with UNION ALL, then ranked together by
#     vector distance so results from different sources are comparable in
#     one ranked list.
#     """
#     if source_type_in:
#         tables = [TABLE_BY_SOURCE_TYPE[SourceType(s)] for s in source_type_in]
#     else:
#         tables = ALL_TABLES

#     clauses = ["curation_status IN ('AutoApproved', 'SMEApproved')"]
#     filter_params: list = []
#     if module:
#         clauses.append("module = %s")
#         filter_params.append(module)
#     if category:
#         clauses.append("category = %s")
#         filter_params.append(category)
#     if severity_in:
#         clauses.append("severity = ANY(%s)")
#         filter_params.append(severity_in)
#     where_sql = " AND ".join(clauses)

#     # Each branch of the UNION ALL carries its own source_type literal
#     # (the column no longer physically exists once split across tables)
#     # plus its own embedding <=> query distance.
#     source_type_by_table = {v: k.value for k, v in TABLE_BY_SOURCE_TYPE.items()}
#     branches = []
#     params: list = []
#     for table in tables:
#         branches.append(f"""
#             SELECT record_id, '{source_type_by_table[table]}' AS source_type, source_ref_id,
#                    module, category, severity, resolution_type, title, structured_sop,
#                    event_date, embedding <=> %s::vector AS distance
#             FROM {table}
#             WHERE {where_sql}
#         """)
#         params.append(Vector(query_embedding))
#         params.extend(filter_params)

#     sql = " UNION ALL ".join(branches) + " ORDER BY distance ASC LIMIT %s;"
#     params.append(top_k)

#     with conn.cursor() as cur:
#         cur.execute(sql, params)
#         cols = [desc[0] for desc in cur.description]
#         return [dict(zip(cols, row)) for row in cur.fetchall()]


"""
Stage 4: Knowledge Store (PostgreSQL + pgvector)
(diagram box: "A combined vector + structured knowledge store (SOPs,
runbooks, KB articles) -- refreshed continuously as tickets close and
re-synced on every Guidewire release cycle.")

Table layout: ONE TABLE PER SOURCE, each with its own vector(1536)
embedding column:
    ticket_records        <- HistoricalTicket
    chat_rca_records      <- ChatOrRCAReport
    release_note_records  <- GuidewireReleaseNote
    runbook_records       <- RunbookOrSOP

Each table shares the same "spine" of columns (taxonomy tags,
structured_sop, embedding, curation status) plus a JSONB
`source_metadata` column holding whatever extra fields only make sense
for that source (a ticket's priority/assignee_team, a runbook's
owner_team/steps, etc).

Retrieval across the "one KB, four tier agents" pattern is done with a
UNION ALL across whichever tables a given query cares about -- see
search_similar(). An agent that only wants ticket+runbook history (L1)
unions 2 tables; an agent that wants everything (L2) unions all 4.

Connection:
  Reads individual connection components from environment variables
  (via .env), rather than one combined URL -- avoids any password
  special-character/URL-encoding issues and matches this team's existing
  .env convention (see PG_HOST / PG_PORT / PG_DATABASE / PG_USER /
  PG_PASSWORD / PG_SSLMODE below).

Guardrail:
  upsert_records() only accepts records whose curation_status is
  AutoApproved or SMEApproved -- it refuses to write anything the SME
  Curation Gate didn't clear.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv
from pgvector import Vector
from pgvector.psycopg import register_vector

from schemas import NormalizedRecord
from taxonomy import CurationStatus, SourceType

load_dotenv()  # reads .env in the project root, if present, into os.environ

ELIGIBLE_FOR_STORE = {CurationStatus.AUTO_APPROVED, CurationStatus.SME_APPROVED}

# Maps each source type to the table that holds it.
TABLE_BY_SOURCE_TYPE: dict[SourceType, str] = {
    SourceType.TICKET: "ticket_records",
    SourceType.CHAT_RCA: "chat_rca_records",
    SourceType.RELEASE_NOTE: "release_note_records",
    SourceType.RUNBOOK: "runbook_records",
}
ALL_TABLES = list(TABLE_BY_SOURCE_TYPE.values())


def _pg_connect_kwargs(dbname: str | None = None) -> dict:
    """Builds psycopg.connect()'s keyword args from individual PG_* env
    vars (read from .env). Passing kwargs directly -- rather than
    building a postgresql://... URL string -- sidesteps any issue with
    special characters (@, :, /, #, etc.) in PG_PASSWORD needing
    URL-encoding."""
    host = os.environ.get("PG_HOST")
    port = os.environ.get("PG_PORT", "5432")
    user = os.environ.get("PG_USER")
    password = os.environ.get("PG_PASSWORD")
    database = dbname or os.environ.get("PG_DATABASE")
    sslmode = os.environ.get("PG_SSLMODE")  # optional, e.g. "require"

    missing = [
        name for name, val in [("PG_HOST", host), ("PG_USER", user),
                                ("PG_PASSWORD", password), ("PG_DATABASE", database)]
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required .env variable(s): {', '.join(missing)}.\n"
            "Copy .env.example to .env and fill in PG_HOST / PG_PORT / PG_DATABASE / "
            "PG_USER / PG_PASSWORD (PG_SSLMODE is optional)."
        )

    kwargs = {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "dbname": database,
    }
    if sslmode:
        kwargs["sslmode"] = sslmode
    return kwargs


def get_connection() -> psycopg.Connection:
    conn = psycopg.connect(**_pg_connect_kwargs(), autocommit=False)
    # Registers the Vector <-> pgvector(n) adapter on this connection, so
    # Vector(...)-wrapped lists can be passed directly as query params and
    # read back as vectors. On a BRAND NEW database (before step2's
    # `CREATE EXTENSION vector` has ever run), the "vector" type doesn't
    # exist yet, so this raises -- that's fine here specifically, since
    # the very next thing that happens on a fresh DB is init_schema(),
    # which creates the extension and re-registers it itself. Any other
    # script calling get_connection() is assumed to run against a DB
    # that already has the extension (i.e. step2 has already been run).
    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        conn.rollback()
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    # The vector type only exists after schema.sql runs, so the adapter
    # must be (re)registered on this connection now that it's live.
    register_vector(conn)


def create_database_if_not_exists() -> str:
    """Step 1 of the manual workflow: CREATE DATABASE for PG_DATABASE (from
    .env), if it doesn't already exist.

    CREATE DATABASE cannot run inside a transaction and cannot run on
    the connection you're about to use the new database from -- so this
    connects to Postgres's own always-present "postgres" maintenance
    database (same host/user/password/port, just a different dbname),
    in autocommit mode, to issue the CREATE DATABASE statement. Returns
    the target database name.
    """
    target_db = os.environ.get("PG_DATABASE")
    if not target_db:
        raise RuntimeError(
            "PG_DATABASE is not set. Copy .env.example to .env and fill in "
            "PG_HOST / PG_PORT / PG_DATABASE / PG_USER / PG_PASSWORD."
        )

    conn = psycopg.connect(**_pg_connect_kwargs(dbname="postgres"), autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
            exists = cur.fetchone() is not None
            if exists:
                print(f"Database '{target_db}' already exists -- nothing to do.")
            else:
                # Database names can't be parameterized in CREATE DATABASE;
                # psycopg's sql.Identifier safely quotes it instead of
                # string-formatting it in directly.
                from psycopg import sql

                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
                print(f"Database '{target_db}' created.")
    finally:
        conn.close()

    return target_db


def insert_records_no_embedding(
    conn: psycopg.Connection,
    records: list[NormalizedRecord],
    kb_version: str,
) -> dict[str, int]:
    """Writes freshly-ingested/tagged/structured records into their tables
    WITHOUT an embedding (embedding column is left NULL on insert) --
    regardless of curation_status. Every record coming out of the real
    pipeline is PENDING_REVIEW (see curation.py), so this is what actually
    makes a record visible to a human reviewer: it sits in its table with
    embedding=NULL and curation_status='PendingReview' until sme_review.py
    approves/rejects/retags it. search_similar() already excludes anything
    that isn't AutoApproved/SMEApproved, so a pending row is never
    retrievable in the meantime.

    On conflict (re-running against an already-populated row), this
    deliberately does NOT touch the embedding or curation_status columns
    -- so re-ingesting a record that's already sitting in the review
    queue (or already reviewed) never wipes out a human's decision or an
    embedding sme_review.py already generated. Only use this for records
    that genuinely need (re)tagging -- ingest_all()'s existing_hashes skip
    already keeps already-decided, unchanged records out of this call.
    """
    written: dict[str, int] = {t: 0 for t in ALL_TABLES}
    with conn.cursor() as cur:
        for r in records:
            table = TABLE_BY_SOURCE_TYPE[r.source_type]
            cur.execute(
                f"""
                INSERT INTO {table} (
                    record_id, source_ref_id, module, category, severity,
                    resolution_type, tagging_confidence, tagging_reasoning, title, raw_text, structured_sop,
                    resolution, guidewire_version, event_date, source_url, source_metadata,
                    curation_status, curation_notes, kb_version, ingested_at, content_hash
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (record_id) DO UPDATE SET
                    source_ref_id = EXCLUDED.source_ref_id,
                    module = EXCLUDED.module,
                    category = EXCLUDED.category,
                    severity = EXCLUDED.severity,
                    resolution_type = EXCLUDED.resolution_type,
                    tagging_confidence = EXCLUDED.tagging_confidence,
                    tagging_reasoning = EXCLUDED.tagging_reasoning,
                    title = EXCLUDED.title,
                    raw_text = EXCLUDED.raw_text,
                    structured_sop = EXCLUDED.structured_sop,
                    resolution = EXCLUDED.resolution,
                    guidewire_version = EXCLUDED.guidewire_version,
                    event_date = EXCLUDED.event_date,
                    source_url = EXCLUDED.source_url,
                    source_metadata = EXCLUDED.source_metadata,
                    kb_version = EXCLUDED.kb_version,
                    ingested_at = EXCLUDED.ingested_at,
                    content_hash = EXCLUDED.content_hash,
                    upserted_at = now();
                    -- embedding and curation_status intentionally NOT in
                    -- this SET list -- see docstring above
                """,
                (
                    r.record_id,
                    r.source_ref_id,
                    r.module.value,
                    r.category.value,
                    r.severity.value,
                    r.resolution_type.value if r.resolution_type else None,
                    r.tagging_confidence,
                    r.tagging_reasoning,
                    r.title,
                    r.raw_text,
                    r.structured_sop,
                    r.resolution_text,
                    r.guidewire_version,
                    r.event_date,
                    r.source_url,
                    json.dumps(r.source_metadata) if r.source_metadata is not None else None,
                    r.curation_status.value,
                    r.curation_notes,
                    kb_version,
                    r.ingested_at,
                    r.content_hash,
                ),
            )
            written[table] += 1
    conn.commit()
    return written


def fetch_rows_missing_embedding(conn: psycopg.Connection) -> dict[str, list[tuple[str, str, str]]]:
    """Step 4 of the manual workflow, part 1: find every row across all
    4 tables that's cleared for embedding -- i.e. AutoApproved/SMEApproved
    AND still has embedding IS NULL. A PendingReview/SMERejected/
    NeedsRetag row is deliberately excluded: nothing gets embedded before
    a human has approved it. A ticket_records row whose status is
    "InReview" (not yet "Closed") is also excluded -- it's auto-approved
    but not final yet, so it must not get embedded here either (same rule
    pipeline.py applies on ingestion). Other tables have no "status" key
    in source_metadata, so this condition is always true for them.
    Returns {table_name: [(record_id, title, raw_text), ...]}."""
    pending: dict[str, list[tuple[str, str, str]]] = {}
    with conn.cursor() as cur:
        for table in ALL_TABLES:
            cur.execute(
                f"""SELECT record_id, title, raw_text FROM {table}
                    WHERE embedding IS NULL
                      AND curation_status IN ('AutoApproved', 'SMEApproved')
                      AND (source_metadata->>'status' IS NULL OR source_metadata->>'status' = 'Closed')"""
            )
            pending[table] = cur.fetchall()
    return pending


def fetch_pending_review(conn: psycopg.Connection) -> list[dict]:
    """Returns every row across all 4 tables still awaiting a human
    decision (curation_status='PendingReview'), for sme_review.py to show
    a reviewer. Each dict includes which table it came from, since the
    reviewer's decision has to be written back to that same table."""
    rows: list[dict] = []
    with conn.cursor() as cur:
        for table in ALL_TABLES:
            cur.execute(
                f"""SELECT record_id, source_ref_id, module, category, severity,
                           resolution_type, tagging_confidence, tagging_reasoning,
                           title, raw_text, structured_sop, kb_version
                    FROM {table}
                    WHERE curation_status = 'PendingReview'
                    ORDER BY ingested_at"""
            )
            cols = [desc[0] for desc in cur.description]
            for row in cur.fetchall():
                record = dict(zip(cols, row))
                record["table"] = table
                rows.append(record)
    return rows


def set_curation_decision(
    conn: psycopg.Connection,
    table: str,
    record_id: str,
    status: CurationStatus,
    notes: str,
) -> None:
    """Writes a human reviewer's decision (sme_review.py) back to the one
    row it concerns. Does NOT touch embedding -- approving a record and
    generating its embedding are deliberately two separate calls (see
    sme_review.py), so a caller can never accidentally mark something
    SMEApproved without actually having embedded it right after."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {table} SET curation_status = %s, curation_notes = %s WHERE record_id = %s",
            (status.value, notes, record_id),
        )
    conn.commit()


def register_approved_hash(conn: psycopg.Connection, table: str, record_id: str, kb_version: str) -> None:
    """After sme_review.py approves a record, records its content_hash
    (already stored on the row itself -- see insert_records_no_embedding)
    into ingestion_state, so the next pipeline run's duplicate-run
    prevention skips re-tagging it. Mirrors upsert_ingestion_hashes(), but
    reads the hash back from the row instead of requiring the in-memory
    NormalizedRecord (which no longer exists by the time a human reviews
    a record written in an earlier run)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT content_hash FROM {table} WHERE record_id = %s", (record_id,))
        row = cur.fetchone()
        if row is None or row[0] is None:
            return
        content_hash = row[0]
        source_type = next(st.value for st, t in TABLE_BY_SOURCE_TYPE.items() if t == table)
        cur.execute(
            """INSERT INTO ingestion_state (record_id, source_type, content_hash, kb_version)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (record_id) DO UPDATE SET
                   source_type = EXCLUDED.source_type,
                   content_hash = EXCLUDED.content_hash,
                   last_ingested_at = now(),
                   kb_version = EXCLUDED.kb_version""",
            (record_id, source_type, content_hash, kb_version),
        )
    conn.commit()


def clear_ingestion_hash(conn: psycopg.Connection, record_id: str) -> None:
    """Removes a record's entry from ingestion_state, if any. Called by
    sme_review.py when a record is rejected or flagged NeedsRetag, so the
    next pipeline run does NOT treat it as "already processed, unchanged,
    skip it" -- it keeps getting retagged on every run until it's either
    approved or its raw content actually changes, same intent as before
    pipeline.py/step3_populate_data.py started registering hashes for
    every ingested record (not just approved ones)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ingestion_state WHERE record_id = %s", (record_id,))
    conn.commit()


def update_embedding(conn: psycopg.Connection, table: str, record_id: str, embedding: list[float]) -> None:
    """Step 4 of the manual workflow, part 2: write a generated embedding
    into one specific row's embedding column."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {table} SET embedding = %s WHERE record_id = %s",
            (Vector(embedding), record_id),
        )


def new_kb_version() -> str:
    """One version id per refresh run -- written to kb_refresh_log and
    stamped on every record upserted in that run, so you can always
    answer 'what changed in the last sync'."""
    return f"kb-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _upsert_sql_for(table: str) -> str:
    return f"""
        INSERT INTO {table} (
            record_id, source_ref_id, module, category, severity,
            resolution_type, tagging_confidence, tagging_reasoning, title, raw_text, structured_sop,
            resolution, guidewire_version, event_date, embedding, source_url, source_metadata,
            curation_status, curation_notes, kb_version, ingested_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (record_id) DO UPDATE SET
            source_ref_id = EXCLUDED.source_ref_id,
            module = EXCLUDED.module,
            category = EXCLUDED.category,
            severity = EXCLUDED.severity,
            resolution_type = EXCLUDED.resolution_type,
            tagging_confidence = EXCLUDED.tagging_confidence,
            tagging_reasoning = EXCLUDED.tagging_reasoning,
            title = EXCLUDED.title,
            raw_text = EXCLUDED.raw_text,
            structured_sop = EXCLUDED.structured_sop,
            resolution = EXCLUDED.resolution,
            guidewire_version = EXCLUDED.guidewire_version,
            event_date = EXCLUDED.event_date,
            embedding = EXCLUDED.embedding,
            source_url = EXCLUDED.source_url,
            source_metadata = EXCLUDED.source_metadata,
            curation_status = EXCLUDED.curation_status,
            curation_notes = EXCLUDED.curation_notes,
            kb_version = EXCLUDED.kb_version,
            ingested_at = EXCLUDED.ingested_at,
            upserted_at = now();
    """


def upsert_records(
    conn: psycopg.Connection,
    records: list[NormalizedRecord],
    kb_version: str,
) -> dict[str, int]:
    """Insert or refresh records, routing each one into its source's own
    table. Rejects any record the SME Curation Gate has not cleared.
    Returns a dict of {table_name: rows_written}."""
    rejected = [r for r in records if r.curation_status not in ELIGIBLE_FOR_STORE]
    if rejected:
        ids = ", ".join(r.record_id for r in rejected)
        raise ValueError(
            f"Refusing to upsert {len(rejected)} record(s) that are not "
            f"AutoApproved/SMEApproved: {ids}"
        )

    written: dict[str, int] = {t: 0 for t in ALL_TABLES}
    with conn.cursor() as cur:
        for r in records:
            table = TABLE_BY_SOURCE_TYPE[r.source_type]
            cur.execute(
                _upsert_sql_for(table),
                (
                    r.record_id,
                    r.source_ref_id,
                    r.module.value,
                    r.category.value,
                    r.severity.value,
                    r.resolution_type.value if r.resolution_type else None,
                    r.tagging_confidence,
                    r.tagging_reasoning,
                    r.title,
                    r.raw_text,
                    r.structured_sop,
                    r.resolution_text,
                    r.guidewire_version,
                    r.event_date,
                    Vector(r.embedding),
                    r.source_url,
                    json.dumps(r.source_metadata) if r.source_metadata is not None else None,
                    r.curation_status.value,
                    r.curation_notes,
                    kb_version,
                    r.ingested_at,
                ),
            )
            written[table] += 1
    conn.commit()
    return written


def fetch_ingestion_hashes(conn: psycopg.Connection) -> dict[str, str]:
    """Returns {record_id: content_hash} for every record a prior run fully
    processed (tagged + approved), so ingestion.ingest_all() can skip
    re-running the LLM tagging call for anything whose raw source fields
    haven't changed since then. Returns {} on a brand-new database (table
    doesn't exist yet) instead of raising, so callers can use this before
    init_schema() has necessarily run."""
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT record_id, content_hash FROM ingestion_state")
            return dict(cur.fetchall())
        except psycopg.errors.UndefinedTable:
            conn.rollback()
            return {}


def upsert_ingestion_hashes(
    conn: psycopg.Connection,
    records: list[NormalizedRecord],
    kb_version: str,
) -> None:
    """Records the content hash of every record actually (re)processed this
    run, so the next run can detect which ones are unchanged and skip them.
    Called by pipeline.py/step3_populate_data.py on every ingested record
    (PendingReview included) right after insert -- "already processed"
    means "ran through ingestion/tagging this run", not "already reviewed",
    so a record sitting in the SME queue isn't re-tagged on every future
    run just because no one has reviewed it yet. sme_review.py also calls
    this (via register_approved_hash()) on approval, which is then just a
    no-op re-write of the same hash for an unchanged record. A record that
    comes back NeedsRetag/SMERejected is still never excluded here by this
    function itself -- the caller controls that by what it passes in."""
    rows = [
        (r.record_id, r.source_type.value, r.content_hash, kb_version)
        for r in records
        if r.content_hash is not None
    ]
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO ingestion_state (record_id, source_type, content_hash, kb_version)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (record_id) DO UPDATE SET
                   source_type = EXCLUDED.source_type,
                   content_hash = EXCLUDED.content_hash,
                   last_ingested_at = now(),
                   kb_version = EXCLUDED.kb_version""",
            rows,
        )
    conn.commit()


def log_curation_audit(conn: psycopg.Connection, records: list[NormalizedRecord]) -> None:
    rows = [
        (r.record_id, r.source_type.value, r.tagging_confidence, r.curation_status.value, r.curation_notes)
        for r in records
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO curation_audit_log
               (record_id, source_type, tagging_confidence, curation_status, curation_notes)
               VALUES (%s, %s, %s, %s, %s)""",
            rows,
        )
    conn.commit()


def log_refresh_run(
    conn: psycopg.Connection,
    kb_version: str,
    ingested: int,
    approved: int,
    held_back: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO kb_refresh_log
               (kb_version, records_ingested, records_approved, records_held_back)
               VALUES (%s, %s, %s, %s)""",
            (kb_version, ingested, approved, held_back),
        )
    conn.commit()


def search_similar(
    conn: psycopg.Connection,
    query_embedding: list[float],
    top_k: int = 5,
    module: str | None = None,
    category: str | None = None,
    severity_in: list[str] | None = None,
    source_type_in: list[str] | None = None,
) -> list[dict]:
    """
    Core retrieval function every tier agent (L1-L4) calls, with a
    different filter combination per agent -- see query_demo.py.

    source_type_in picks WHICH of the 4 tables to search (defaults to all
    4); module/category/severity_in filter rows within each table. All
    selected tables are combined with UNION ALL, then ranked together by
    vector distance so results from different sources are comparable in
    one ranked list.
    """
    if source_type_in:
        tables = [TABLE_BY_SOURCE_TYPE[SourceType(s)] for s in source_type_in]
    else:
        tables = ALL_TABLES

    # embedding IS NOT NULL: an auto-approved InReview ticket has no
    # embedding yet (see pipeline.py) and must never be retrievable until
    # it's embedded -- same guarantee as a PendingReview row, just via a
    # different gate (status, not curation_status).
    clauses = ["curation_status IN ('AutoApproved', 'SMEApproved')", "embedding IS NOT NULL"]
    filter_params: list = []
    if module:
        clauses.append("module = %s")
        filter_params.append(module)
    if category:
        clauses.append("category = %s")
        filter_params.append(category)
    if severity_in:
        clauses.append("severity = ANY(%s)")
        filter_params.append(severity_in)
    where_sql = " AND ".join(clauses)

    # Each branch of the UNION ALL carries its own source_type literal
    # (the column no longer physically exists once split across tables)
    # plus its own embedding <=> query distance.
    source_type_by_table = {v: k.value for k, v in TABLE_BY_SOURCE_TYPE.items()}
    branches = []
    params: list = []
    for table in tables:
        branches.append(f"""
            SELECT record_id, '{source_type_by_table[table]}' AS source_type, source_ref_id,
                   module, category, severity, resolution_type, title, structured_sop, resolution,
                   event_date, embedding <=> %s::vector AS distance
            FROM {table}
            WHERE {where_sql}
        """)
        params.append(Vector(query_embedding))
        params.extend(filter_params)

    sql = " UNION ALL ".join(branches) + " ORDER BY distance ASC LIMIT %s;"
    params.append(top_k)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
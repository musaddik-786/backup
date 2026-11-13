"""
Stage 4: Knowledge Store (PostgreSQL + pgvector)
(diagram box: "A combined vector + structured knowledge store (SOPs,
runbooks, KB articles) -- refreshed continuously as tickets close and
re-synced on every Guidewire release cycle.")

Table layout: ONE TABLE PER SOURCE, each with its own vector(3072)
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

    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
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
    """Step 3 of the manual workflow: write tagged/structured records
    into their tables WITHOUT an embedding (embedding column is left
    NULL on first insert). Embeddings get filled in afterward by
    step4_store_embeddings.py via update_embedding().

    On conflict (re-running against an already-populated row), this
    deliberately does NOT touch the embedding column -- so re-running
    step3 never wipes out embeddings step4 already stored.
    """
    rejected = [r for r in records if r.curation_status not in ELIGIBLE_FOR_STORE]
    if rejected:
        ids = ", ".join(r.record_id for r in rejected)
        raise ValueError(
            f"Refusing to insert {len(rejected)} record(s) that are not "
            f"AutoApproved/SMEApproved: {ids}"
        )

    written: dict[str, int] = {t: 0 for t in ALL_TABLES}
    with conn.cursor() as cur:
        for r in records:
            table = TABLE_BY_SOURCE_TYPE[r.source_type]
            cur.execute(
                f"""
                INSERT INTO {table} (
                    record_id, source_ref_id, module, category, severity,
                    resolution_type, tagging_confidence, tagging_reasoning, title, raw_text, structured_sop,
                    guidewire_version, event_date, source_url, source_metadata,
                    curation_status, curation_notes, kb_version, ingested_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
                    guidewire_version = EXCLUDED.guidewire_version,
                    event_date = EXCLUDED.event_date,
                    source_url = EXCLUDED.source_url,
                    source_metadata = EXCLUDED.source_metadata,
                    curation_status = EXCLUDED.curation_status,
                    curation_notes = EXCLUDED.curation_notes,
                    kb_version = EXCLUDED.kb_version,
                    ingested_at = EXCLUDED.ingested_at,
                    upserted_at = now();
                    -- embedding intentionally NOT in this SET list
                """,
                (
                    r.record_id,
                    r.source_ref_id,
                    r.module.value,
                    r.category.value,
                    r.severity.value,
                    r.resolution_type.value,
                    r.tagging_confidence,
                    r.tagging_reasoning,
                    r.title,
                    r.raw_text,
                    r.structured_sop,
                    r.guidewire_version,
                    r.event_date,
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


def fetch_rows_missing_embedding(conn: psycopg.Connection) -> dict[str, list[tuple[str, str, str]]]:
    """Step 4 of the manual workflow, part 1: find every row across all
    4 tables that doesn't have an embedding yet. Returns
    {table_name: [(record_id, title, raw_text), ...]}."""
    pending: dict[str, list[tuple[str, str, str]]] = {}
    with conn.cursor() as cur:
        for table in ALL_TABLES:
            cur.execute(
                f"SELECT record_id, title, raw_text FROM {table} WHERE embedding IS NULL"
            )
            pending[table] = cur.fetchall()
    return pending


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
            guidewire_version, event_date, embedding, source_url, source_metadata,
            curation_status, curation_notes, kb_version, ingested_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
                    r.resolution_type.value,
                    r.tagging_confidence,
                    r.tagging_reasoning,
                    r.title,
                    r.raw_text,
                    r.structured_sop,
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

    clauses = ["curation_status IN ('AutoApproved', 'SMEApproved')"]
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
                   module, category, severity, resolution_type, title, structured_sop,
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

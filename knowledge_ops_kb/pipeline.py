"""
Orchestrates the full pipeline exactly as laid out in the architecture
diagram:

    4 raw sources
        -> Ingestion & Normalization   (ingestion.py)
        -> Structuring Agent            (structuring.py)
        -> SME Curation Gate            (curation.py)
        -> Knowledge Store              (knowledge_store.py, Postgres+pgvector)

Run with:
    Fill in .env (copy .env.example) with PG_HOST / PG_PORT / PG_DATABASE / PG_USER / PG_PASSWORD
    python pipeline.py

This is the "refresh" job referenced in the diagram footer:
"refreshed continuously as tickets close and re-synced on every Guidewire
release cycle" -- in production this runs on a schedule (or is triggered
by a ticket-closed / release-published webhook) rather than manually.
"""

from __future__ import annotations

import sys

from curation import run_curation_gate
from ingestion import ingest_all
from knowledge_store import (
    get_connection,
    init_schema,
    log_curation_audit,
    log_refresh_run,
    new_kb_version,
    upsert_records,
)
from structuring import structure_and_embed


def run_pipeline() -> None:
    print("=" * 70)
    print("KNOWLEDGE OPS AGENT -- KNOWLEDGE BASE REFRESH")
    print("=" * 70)

    kb_version = new_kb_version()
    print(f"\n[1/4] Ingestion & Normalization  (kb_version={kb_version})")
    records = ingest_all()
    print(f"      -> {len(records)} records ingested from 4 sources")

    print("\n[2/4] Structuring Agent")
    records = structure_and_embed(records)
    print(f"      -> structured SOPs + {len(records[0].embedding)}-dim embeddings generated")

    print("\n[3/4] SME Curation Gate")
    approved, held_back = run_curation_gate(records)
    print(f"      -> {len(approved)} approved, {len(held_back)} held back")
    for r in held_back:
        print(f"         held back: {r.record_id} [{r.curation_status.value}] {r.curation_notes}")

    print("\n[4/4] Knowledge Store (PostgreSQL + pgvector)")
    try:
        conn = get_connection()
    except RuntimeError as e:
        print(f"      SKIPPED -- {e}")
        print("\nPipeline stages 1-3 completed successfully (see above).")
        print("Connect a real Postgres+pgvector instance and re-run to complete stage 4.")
        sys.exit(0)

    try:
        init_schema(conn)
        written = upsert_records(conn, approved, kb_version)
        log_curation_audit(conn, held_back)
        log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))
        for table, count in written.items():
            if count:
                print(f"      -> {count} records upserted into {table}")
        print(f"      -> refresh run logged as {kb_version}")
    finally:
        conn.close()

    print("\nDone. Knowledge Store is ready to serve the L1-L4 tier agents (see query_demo.py).")


if __name__ == "__main__":
    run_pipeline()

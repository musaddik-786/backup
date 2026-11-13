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

    # Selective ingestion -- only refresh specific source(s), e.g. just
    # release notes, or tickets + chat/RCA. Omitting --sources (the
    # default) refreshes all 4, same as always.
    python pipeline.py --sources release_notes
    python pipeline.py --sources tickets,chat_rca

    # Duplicate-run prevention is on by default: a record whose raw
    # content hasn't changed since the last run that fully processed it is
    # skipped entirely (no LLM tagging call, no re-embedding). --force
    # bypasses that and re-processes every selected record regardless.
    python pipeline.py --force

This is the "refresh" job referenced in the diagram footer:
"refreshed continuously as tickets close and re-synced on every Guidewire
release cycle" -- in production this runs on a schedule (or is triggered
by a ticket-closed / release-published webhook) rather than manually.
"""

from __future__ import annotations

import argparse
import sys

from curation import run_curation_gate
from ingestion import SOURCE_KEY_TO_TYPE, ingest_all
from knowledge_store import (
    fetch_ingestion_hashes,
    get_connection,
    init_schema,
    log_curation_audit,
    log_refresh_run,
    new_kb_version,
    upsert_ingestion_hashes,
    upsert_records,
)
from structuring import structure_and_embed
from taxonomy import SourceType


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot Knowledge Base refresh: ingest, structure+embed, SME-gate, write to Postgres."
    )
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help=(
            f"Comma-separated subset of {{{','.join(SOURCE_KEY_TO_TYPE)}}} to refresh. "
            "Omit to refresh all 4 (default, unchanged behavior)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-tag and re-embed every selected record even if unchanged since the last run.",
    )
    return parser.parse_args(argv)


def run_pipeline(sources: list[SourceType] | None = None, force: bool = False) -> None:
    print("=" * 70)
    print("KNOWLEDGE OPS AGENT -- KNOWLEDGE BASE REFRESH")
    print("=" * 70)

    kb_version = new_kb_version()

    # Connect early (the same connection is reused for Stage 4 below) so
    # already-unchanged records can skip re-tagging/re-embedding, not just
    # re-insertion. Graceful degradation is unchanged from before: if
    # Postgres isn't reachable, stages 1-3 still run and report results,
    # and the same "SKIPPED" message prints at the same point (Stage 4).
    conn = None
    conn_error: RuntimeError | None = None
    try:
        conn = get_connection()
        init_schema(conn)
    except RuntimeError as e:
        conn_error = e
        conn = None

    existing_hashes: dict[str, str] = {}
    if conn is not None and not force:
        existing_hashes = fetch_ingestion_hashes(conn)

    print(f"\n[1/4] Ingestion & Normalization  (kb_version={kb_version})")
    records = ingest_all(sources=sources, existing_hashes=existing_hashes)
    if not records:
        print("      -> nothing new or changed for the selected source(s)")
        print("\nNothing to refresh -- Knowledge Store is already up to date.")
        if conn is not None:
            conn.close()
        return
    print(f"      -> {len(records)} record(s) ingested/changed")

    print("\n[2/4] Structuring Agent")
    records = structure_and_embed(records)
    print(f"      -> structured SOPs + {len(records[0].embedding)}-dim embeddings generated")

    print("\n[3/4] SME Curation Gate")
    approved, held_back = run_curation_gate(records)
    print(f"      -> {len(approved)} approved, {len(held_back)} held back")
    for r in held_back:
        print(f"         held back: {r.record_id} [{r.curation_status.value}] {r.curation_notes}")

    print("\n[4/4] Knowledge Store (PostgreSQL + pgvector)")
    if conn is None:
        print(f"      SKIPPED -- {conn_error}")
        print("\nPipeline stages 1-3 completed successfully (see above).")
        print("Connect a real Postgres+pgvector instance and re-run to complete stage 4.")
        sys.exit(0)

    try:
        written = upsert_records(conn, approved, kb_version)
        log_curation_audit(conn, held_back)
        log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))
        upsert_ingestion_hashes(conn, approved, kb_version)
        for table, count in written.items():
            if count:
                print(f"      -> {count} records upserted into {table}")
        print(f"      -> refresh run logged as {kb_version}")
    finally:
        conn.close()

    print("\nDone. Knowledge Store is ready to serve the L1-L4 tier agents (see query_demo.py).")


if __name__ == "__main__":
    args = _parse_args()
    selected_sources = None
    if args.sources:
        selected_sources = [SOURCE_KEY_TO_TYPE[s.strip()] for s in args.sources.split(",")]
    run_pipeline(sources=selected_sources, force=args.force)

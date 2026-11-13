"""
STEP 3 of 4: Ingest the 4 raw sources, tag them, render SOP text, run
the SME Curation Gate, and INSERT the approved rows into their tables --
WITHOUT embeddings yet. Every row's `embedding` column is left NULL
after this step; step 4 fills it in separately.

Run:
    python step3_populate_data.py

    # Selective ingestion -- only process specific source(s). Omitting
    # --sources (the default) processes all 4, same as always.
    python step3_populate_data.py --sources release_notes
    python step3_populate_data.py --sources tickets,chat_rca

    # Duplicate-run prevention is on by default: a record whose raw
    # content hasn't changed since the last run that fully processed it is
    # skipped entirely (no LLM tagging call). --force bypasses that and
    # re-tags every selected record regardless.
    python step3_populate_data.py --force
"""

import argparse

from curation import run_curation_gate
from ingestion import SOURCE_KEY_TO_TYPE, ingest_all
from knowledge_store import (
    fetch_ingestion_hashes,
    get_connection,
    insert_records_no_embedding,
    log_curation_audit,
    log_refresh_run,
    new_kb_version,
    upsert_ingestion_hashes,
)
from structuring import render_sops
from taxonomy import SourceType


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Step 3: ingest + tag + curate + insert (no embeddings yet)."
    )
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help=(
            f"Comma-separated subset of {{{','.join(SOURCE_KEY_TO_TYPE)}}} to ingest. "
            "Omit to ingest all 4 (default, unchanged behavior)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-tag every selected record even if unchanged since the last run.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    selected_sources: list[SourceType] | None = None
    if args.sources:
        selected_sources = [SOURCE_KEY_TO_TYPE[s.strip()] for s in args.sources.split(",")]

    kb_version = new_kb_version()

    conn = get_connection()
    try:
        existing_hashes = {} if args.force else fetch_ingestion_hashes(conn)

        print("Ingesting + tagging selected source(s)..." if selected_sources else "Ingesting + tagging all 4 sources...")
        records = ingest_all(sources=selected_sources, existing_hashes=existing_hashes)
        print(f"  -> {len(records)} record(s) need (re)processing this run")

        if not records:
            print("Nothing new or changed -- nothing to render, curate, or insert.")
        else:
            print("Rendering structured SOP text (no embeddings yet)...")
            records = render_sops(records)

            print("Running SME Curation Gate...")
            approved, held_back = run_curation_gate(records)
            print(f"  -> {len(approved)} approved, {len(held_back)} held back")

            written = insert_records_no_embedding(conn, approved, kb_version)
            log_curation_audit(conn, held_back)
            log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))
            upsert_ingestion_hashes(conn, approved, kb_version)

            for table, count in written.items():
                if count:
                    print(f"  -> {count} rows inserted into {table} (embedding = NULL)")
    finally:
        conn.close()

    print("\nStep 3 complete. Data is in the tables, embeddings are NOT yet set.")
    print("Next: python step4_store_embeddings.py")

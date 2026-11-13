"""
STEP 3 of 4: Ingest the 4 raw sources, tag them, render SOP text, run
the SME Curation Gate, and INSERT the approved rows into their tables --
WITHOUT embeddings yet. Every row's `embedding` column is left NULL
after this step; step 4 fills it in separately.

Run:
    python step3_populate_data.py
"""

from curation import run_curation_gate
from ingestion import ingest_all
from knowledge_store import (
    get_connection,
    insert_records_no_embedding,
    log_curation_audit,
    log_refresh_run,
    new_kb_version,
)
from structuring import render_sops

if __name__ == "__main__":
    kb_version = new_kb_version()

    print("Ingesting + tagging all 4 sources...")
    records = ingest_all()
    print(f"  -> {len(records)} records ingested")

    print("Rendering structured SOP text (no embeddings yet)...")
    records = render_sops(records)

    print("Running SME Curation Gate...")
    approved, held_back = run_curation_gate(records)
    print(f"  -> {len(approved)} approved, {len(held_back)} held back")

    conn = get_connection()
    try:
        written = insert_records_no_embedding(conn, approved, kb_version)
        log_curation_audit(conn, held_back)
        log_refresh_run(conn, kb_version, len(records), len(approved), len(held_back))

        for table, count in written.items():
            if count:
                print(f"  -> {count} rows inserted into {table} (embedding = NULL)")
    finally:
        conn.close()

    print("\nStep 3 complete. Data is in the tables, embeddings are NOT yet set.")
    print("Next: python step4_store_embeddings.py")

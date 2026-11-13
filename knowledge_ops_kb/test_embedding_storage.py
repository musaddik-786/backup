"""
FOCUSED TEST: generate one embedding per table, store it in the correct
column of the correct table, then read it straight back from Postgres to
prove it round-trips correctly.

This does NOT run the full pipeline (no SME gate reporting, no bulk
upsert of all 34 records) -- it's the minimum code needed to answer one
question: "does an embedding I generate in Python actually land correctly
in a pgvector column, and can I get it back out?"

Same core mechanism as your Underwriting Solution's
Jarvis/MCP/Document_Query_MCP/query_retriever_pgvector.py (register_vector
+ `embedding <=> %s::vector` cosine search) -- just using psycopg3 instead
of psycopg2, which is what this project's knowledge_store.py already uses.

Run:
    Fill in .env (copy .env.example) with PG_HOST / PG_PORT / PG_DATABASE / PG_USER / PG_PASSWORD
    python test_embedding_storage.py
"""

from __future__ import annotations

from curation import run_curation_gate
from ingestion import ingest_all
from knowledge_store import (
    TABLE_BY_SOURCE_TYPE,
    get_connection,
    init_schema,
    upsert_records,
)
from structuring import structure_and_embed
from taxonomy import SourceType


def pick_one_record_per_source(records):
    """Grab exactly one approved record per source type, so we exercise
    all 4 tables without writing everything."""
    picked = {}
    for r in records:
        if r.source_type not in picked:
            picked[r.source_type] = r
    return picked


def main():
    print("=" * 70)
    print("FOCUSED TEST: embedding generation -> Postgres storage -> read-back")
    print("=" * 70)

    # --- Generate embeddings (stages 1-2, no DB involved yet) ---
    print("\n[1/3] Generating tags + embeddings for all source records...")
    records = structure_and_embed(ingest_all())
    approved, _ = run_curation_gate(records)
    print(f"      {len(approved)} records ready (embeddings already computed in memory)")

    one_per_source = pick_one_record_per_source(approved)
    print(f"      picked {len(one_per_source)} representative records, one per table:")
    for source_type, r in one_per_source.items():
        print(f"        {TABLE_BY_SOURCE_TYPE[source_type]:<22} <- {r.record_id}  "
              f"(embedding length = {len(r.embedding)})")

    # --- Connect + write just these 4 rows ---
    print("\n[2/3] Connecting to Postgres and writing these rows...")
    try:
        conn = get_connection()
    except RuntimeError as e:
        print(f"      SKIPPED -- {e}")
        print("\nEmbedding generation confirmed working (step 1). Fill in .env with your Postgres details")
        print("and re-run to confirm storage + read-back against a real Postgres.")
        return

    try:
        init_schema(conn)
        written = upsert_records(conn, list(one_per_source.values()), kb_version="test-embed-storage")
        for table, count in written.items():
            if count:
                print(f"      wrote {count} row into {table}")

        # --- Read each row straight back and verify the embedding ---
        print("\n[3/3] Reading each row back from Postgres to verify the embedding...")
        with conn.cursor() as cur:
            for source_type, r in one_per_source.items():
                table = TABLE_BY_SOURCE_TYPE[source_type]
                cur.execute(
                    f"SELECT record_id, embedding FROM {table} WHERE record_id = %s",
                    (r.record_id,),
                )
                row = cur.fetchone()
                if row is None:
                    print(f"      FAILED: {r.record_id} not found in {table}")
                    continue

                record_id, stored_embedding = row
                # stored_embedding comes back as a pgvector.Vector; compare
                # its values against what we generated in Python.
                stored_list = list(stored_embedding)
                matches = len(stored_list) == len(r.embedding) and all(
                    abs(a - b) < 1e-6 for a, b in zip(stored_list, r.embedding)
                )
                print(
                    f"      {table:<22} {record_id:<18} "
                    f"stored dim={len(stored_list)}  "
                    f"roundtrip match={'YES' if matches else 'NO -- MISMATCH!'}"
                )
    finally:
        conn.close()

    print("\nDone. If every row above says 'roundtrip match=YES', embedding")
    print("generation and pgvector storage are both confirmed working end to end.")


if __name__ == "__main__":
    main()

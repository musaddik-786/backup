"""
STEP 4 of 4: Find every row (across all 4 tables) that doesn't have an
embedding yet, generate one for each, and write it back with an UPDATE.

This is the step that actually fills the `embedding vector(3072)` column.
It reads `title` + `raw_text` straight back out of Postgres (not from
Python memory) -- a true "backfill" step, decoupled from step 3.

Run:
    python step4_store_embeddings.py
"""

from knowledge_store import fetch_rows_missing_embedding, get_connection, update_embedding
from structuring import fit_embeddings

if __name__ == "__main__":
    conn = get_connection()
    try:
        print("Finding rows without an embedding yet...")
        pending = fetch_rows_missing_embedding(conn)

        texts: list[str] = []
        index: list[tuple[str, str]] = []  # (table, record_id), aligned with `texts`
        for table, rows in pending.items():
            for record_id, title, raw_text in rows:
                texts.append(f"{title} {raw_text}")
                index.append((table, record_id))
            if rows:
                print(f"  -> {len(rows)} rows pending in {table}")

        if not texts:
            print("Nothing to embed -- every row already has an embedding.")
            raise SystemExit(0)

        print(f"\nGenerating embeddings for {len(texts)} rows "
              f"(fitting one shared vector space across all of them)...")
        embeddings = fit_embeddings(texts)
        print(f"  -> {len(embeddings)} embeddings generated, {len(embeddings[0])} dimensions each")

        print("\nWriting each embedding back to its row...")
        for (table, record_id), embedding in zip(index, embeddings):
            update_embedding(conn, table, record_id, embedding)
        conn.commit()

        print(f"\nStep 4 complete. {len(index)} rows now have embeddings stored.")
        print("Run `python query_demo.py` to see the 4 tier agents query this data.")
    finally:
        conn.close()

"""
STEP 2 of 4: Create the pgvector extension + the 4 tables
(ticket_records, chat_rca_records, release_note_records, runbook_records)
plus the shared curation_audit_log / kb_refresh_log tables.

No data is written in this step -- tables are created empty.

Run:
    python step2_create_tables.py
"""

from knowledge_store import get_connection, init_schema

if __name__ == "__main__":
    conn = get_connection()
    try:
        init_schema(conn)
        print("Step 2 complete. All 4 tables (+ governance tables) created.")
        print("Next: python step3_populate_data.py")
    finally:
        conn.close()

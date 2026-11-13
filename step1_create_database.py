"""
STEP 1 of 4: Create the database (if it doesn't already exist).

This only creates the DATABASE itself -- no tables yet (that's step 2).

Run:
    python step1_create_database.py
"""

from knowledge_store import create_database_if_not_exists

if __name__ == "__main__":
    db_name = create_database_if_not_exists()
    print(f"\nStep 1 complete. Database '{db_name}' is ready.")
    print("Next: python step2_create_tables.py")

"""Create a NEW Postgres database (default: <current>_1536) and apply db/schema.sql (vector(1536)).

The existing database is never altered: this only connects to it (or 'postgres') to run CREATE DATABASE,
then applies the schema in the new database. Re-run safe (schema uses IF NOT EXISTS).
Usage: python db/create_new_db.py [--name NEWNAME] [--dry-run]
Then set PG_DATABASE / AZURE_PG_DATABASE in .env to the new name and re-run backend step3/step4.
"""
import argparse, os, re, sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    base = os.environ.get("PG_DATABASE") or os.environ.get("AZURE_PG_DATABASE")
    name = a.name or f"{base}_1536"
    if not re.fullmatch(r"[A-Za-z0-9_]+", name) or name == base:
        sys.exit(f"Refusing: new name must be alphanumeric/underscore and differ from existing DB ({base})")
    kw = dict(host=os.environ["PG_HOST"], port=os.environ.get("PG_PORT", "5432"), user=os.environ["PG_USER"],
              password=os.environ["PG_PASSWORD"], sslmode=os.environ.get("PG_SSLMODE", "require"))
    print(f"Existing DB (untouched): {base}\nNew DB: {name}")
    if a.dry_run:
        return 0
    with psycopg.connect(dbname="postgres", autocommit=True, **kw) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
            print("New DB already exists; reusing")
        else:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(dbname=name, **kw) as c:
        c.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    print("Schema applied (vector(1536)).")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

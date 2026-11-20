"""Read-only ticket API (port of the former Express server in ui/server_legacy_express)."""
import json, os
from pathlib import Path
from fastapi import APIRouter, HTTPException
import psycopg
from psycopg.rows import dict_row

router = APIRouter(prefix="/api")
ROOT = Path(__file__).resolve().parents[2]

def get_conn():
    return psycopg.connect(host=os.environ["PG_HOST"], port=os.environ.get("PG_PORT", "5432"),
                           dbname=os.environ["PG_DATABASE"], user=os.environ["PG_USER"],
                           password=os.environ["PG_PASSWORD"], sslmode=os.environ.get("PG_SSLMODE", "require"),
                           row_factory=dict_row)

TICKET_SQL = """SELECT record_id, source_ref_id, module, category, severity, resolution_type, title, raw_text,
 structured_sop, guidewire_version, event_date, source_metadata, curation_status, curation_notes, kb_version, ingested_at, source_url
 FROM ticket_records WHERE COALESCE(source_metadata ->> 'removed', 'false') <> 'true' ORDER BY (source_metadata ->> 'created_at')::timestamp DESC NULLS LAST"""

def _query(q: str):
    try:
        with get_conn() as c:
            return c.execute(q).fetchall()
    except Exception as exc:
        raise HTTPException(500, "Database query failed") from exc

@router.get("/tickets")
def tickets():
    return _query(TICKET_SQL)

@router.get("/modules")
def modules():
    return [r["module"] for r in _query("SELECT DISTINCT module FROM ticket_records WHERE COALESCE(source_metadata ->> 'removed', 'false') <> 'true' ORDER BY module")]

@router.get("/assignee-teams")
def assignee_teams():
    path = Path(os.environ.get("RAW_TICKETS_JSON", ROOT / "data" / "raw" / "tickets.json"))
    try:
        return sorted({t["assignee_team"] for t in json.loads(path.read_text(encoding="utf-8"))})
    except Exception as exc:
        raise HTTPException(500, "Failed to read assignee teams") from exc

"""Sync tickets from osTicket (source of truth) into Postgres `ticket_records`.

osTicket's HTTP API is create-only, so we read its MySQL database directly with a
read-only user. Each osTicket ticket becomes one ticket_records row (record_id =
'osticket-<ticket_id>'). Re-running is safe: title/body/priority/status are refreshed,
but AI tagging and curation fields on existing rows are never overwritten.
"""
import html, json, os, re
import pymysql
import psycopg
from psycopg.types.json import Jsonb

# osTicket priority_id -> severity (we use osTicket's own priority names everywhere)
SEVERITY = {"1": "Low", "2": "Normal", "3": "High", "4": "Emergency"}
MODULES = {"PolicyCenter", "ClaimCenter", "BillingCenter", "ContactManager",
           "Rating/Gosu", "IntegrationGateway", "DigitalPortal", "DataPlatform"}
# ticket_records requires module/category. Until the AI tagger runs, rows get these
# placeholders and curation_status='NeedsRetag' so they are easy to find and re-tag.
PLACEHOLDER_MODULE, PLACEHOLDER_CATEGORY = "DigitalPortal", "ConfigurationError"

SQL = """
SELECT t.ticket_id, t.number, t.created, t.closed, t.updated, s.name AS status,
       c.subject, c.priority AS priority_id, p.priority AS priority_name,
       h.topic, tm.name AS team,
       (SELECT e.body FROM ost_thread_entry e JOIN ost_thread th ON th.id = e.thread_id
         WHERE th.object_id = t.ticket_id AND th.object_type = 'T' AND e.type = 'M'
         ORDER BY e.id LIMIT 1) AS body
FROM ost_ticket t
JOIN ost_ticket_status s ON s.id = t.status_id
LEFT JOIN ost_ticket__cdata c ON c.ticket_id = t.ticket_id
LEFT JOIN ost_ticket_priority p ON p.priority_id = c.priority
LEFT JOIN ost_help_topic h ON h.topic_id = t.topic_id
LEFT JOIN ost_team tm ON tm.team_id = t.team_id
ORDER BY t.ticket_id"""

UPSERT = """
INSERT INTO ticket_records
 (record_id, source_ref_id, module, category, severity, tagging_confidence, tagging_reasoning,
  title, raw_text, event_date, source_url, source_metadata, curation_status, ingested_at)
VALUES (%(record_id)s, %(number)s, %(module)s, %(category)s, %(severity)s, 0,
  'Synced from osTicket; not yet tagged', %(title)s, %(body)s, %(event_date)s, %(url)s,
  %(meta)s, 'NeedsRetag', now())
ON CONFLICT (record_id) DO UPDATE SET
  title = EXCLUDED.title, raw_text = EXCLUDED.raw_text, severity = EXCLUDED.severity,
  event_date = EXCLUDED.event_date, source_url = EXCLUDED.source_url,
  source_metadata = EXCLUDED.source_metadata, upserted_at = now()
RETURNING (xmax = 0) AS inserted"""


def _clean(body: str | None) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", body or "")
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def fetch_osticket_tickets() -> list[dict]:
    conn = pymysql.connect(host=os.environ.get("OSTICKET_DB_HOST", "127.0.0.1"),
                           port=int(os.environ.get("OSTICKET_DB_PORT", "3306")),
                           user=os.environ["OSTICKET_DB_USER"], password=os.environ["OSTICKET_DB_PASSWORD"],
                           database=os.environ.get("OSTICKET_DB_NAME", "osticket"),
                           cursorclass=pymysql.cursors.DictCursor)
    with conn, conn.cursor() as cur:
        cur.execute(SQL)
        return cur.fetchall()


def _to_row(t: dict) -> dict:
    base = os.environ.get("OSTICKET_URL", "").rstrip("/")
    topic = t["topic"] or ""
    meta = {"source": "osticket", "osticket_ticket_id": t["ticket_id"], "status": t["status"],
            "priority": (t["priority_name"] or "normal").title(), "assignee_team": t["team"],
            "created_at": t["created"].isoformat(),
            "closed_at": t["closed"].isoformat() if t["closed"] else None}
    return {"record_id": f"osticket-{t['ticket_id']}", "number": t["number"],
            "module": topic if topic in MODULES else PLACEHOLDER_MODULE,
            "category": PLACEHOLDER_CATEGORY,
            "severity": SEVERITY.get(str(t["priority_id"]), "Normal"),
            "title": t["subject"] or f"osTicket #{t['number']}",
            "body": _clean(t["body"]) or t["subject"] or "(no body)",
            "event_date": t["created"].date(),
            "url": f"{base}/scp/tickets.php?id={t['ticket_id']}" if base else None,
            "meta": Jsonb(meta)}


def _mark_removed(c, live_ids: list[str]) -> int:
    """Soft-delete synced rows whose osTicket ticket no longer exists.

    Rows are flagged (source_metadata.removed = true), not deleted, and every read path
    filters them out. A ticket that reappears is un-flagged by the next upsert. Skipped
    when osTicket returned nothing, so an empty/misconfigured source cannot hide everything.
    """
    if not live_ids:
        return 0
    return c.execute("""UPDATE ticket_records
        SET source_metadata = COALESCE(source_metadata, '{}'::jsonb)
                              || jsonb_build_object('removed', true, 'removed_at', now()::text),
            upserted_at = now()
        WHERE record_id LIKE 'osticket-%%' AND NOT (record_id = ANY(%s))
          AND COALESCE(source_metadata ->> 'removed', 'false') <> 'true'""", (live_ids,)).rowcount


def sync(pg_conn_factory, dry_run: bool = False) -> dict:
    rows = [_to_row(t) for t in fetch_osticket_tickets()]
    if dry_run:
        return {"found": len(rows), "inserted": 0, "updated": 0, "dry_run": True, "removed": 0,
                "sample": [{k: v for k, v in r.items() if k != "meta"} for r in rows[:3]]}
    inserted = updated = 0
    with pg_conn_factory() as c:
        for r in rows:
            if c.execute(UPSERT, r).fetchone()["inserted"]:
                inserted += 1
            else:
                updated += 1
        removed = _mark_removed(c, [r["record_id"] for r in rows])
    return {"found": len(rows), "inserted": inserted, "updated": updated, "removed": removed,
            "dry_run": False}


if __name__ == "__main__":  # python -m app.services.osticket [--dry-run]
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    from app.routers.tickets import get_conn
    print(json.dumps(sync(get_conn, "--dry-run" in sys.argv), indent=2, default=str))

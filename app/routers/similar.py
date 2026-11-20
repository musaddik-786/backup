"""POST /api/tickets/similar: retrieval-agent matches enriched with the matched tickets' details for the UI."""
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from psycopg import Error as PsycopgError

from retrieval_agent.database import fetch_eligible_tickets
from retrieval_agent.pipeline import retrieve
from retrieval_agent.providers import Settings, complete_json, embed_texts

router = APIRouter(prefix="/api")


class SimilarRequest(BaseModel):
    id: str | None = None
    title: str | None = None
    description: str | None = None
    module: str | None = None
    severity: str | None = None
    created: str | None = None


def _detail(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("source_metadata") or {}
    return {
        "description": record.get("description"),
        "system": record.get("system"),
        "created": str(record["created"]) if record.get("created") else None,
        "closed": str(record["closed"]) if record.get("closed") else None,
        "severity": record.get("severity"),
        "resolution": record.get("resolution"),
        "rca": record.get("rca"),
        "runbook_link": record.get("runbook_link"),
        "assignee_team": meta.get("assignee_team"),
    }


@router.post("/tickets/similar")
def similar_tickets(req: SimilarRequest) -> dict[str, Any]:
    ticket = {
        "id": req.id, "short_description": req.title, "description": req.description,
        "system": req.module, "severity": req.severity, "created": req.created,
    }
    ticket = {k: v for k, v in ticket.items() if v}
    if not (ticket.get("short_description") or ticket.get("description")):
        raise HTTPException(422, "Provide a title or description")
    settings = Settings.load()
    try:
        records = fetch_eligible_tickets(settings)
        if req.id:  # a ticket must not match itself
            records = [r for r in records if r["ticket_id"].casefold() != req.id.casefold()]
        result = retrieve(
            ticket, records, settings,
            lambda text: embed_texts(settings, [text])[0],
            lambda s, u: complete_json(settings, s, u),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    except PsycopgError as exc:
        raise HTTPException(503, f"Database query failed: {exc}") from exc
    by_id = {r["ticket_id"]: r for r in records}
    for m in result.get("matches", []):
        m["ticket"] = _detail(by_id.get(m["id"], {}))
    return result

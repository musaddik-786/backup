"""POST /api/kb/resolve: match a ticket to a KB card (article + runbook play) or say there is no match."""
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from psycopg import Error as PsycopgError

from retrieval_agent.kb_resolver import fetch_units, resolve
from retrieval_agent.providers import Settings, complete_json, embed_texts

router = APIRouter(prefix="/api/kb")


class ResolveRequest(BaseModel):
    id: str | None = None
    title: str | None = None
    description: str | None = None


@router.post("/resolve")
def resolve_ticket(req: ResolveRequest) -> dict[str, Any]:
    ticket = {"title": req.title, "description": req.description}
    if not (req.title or req.description):
        raise HTTPException(422, "Provide a title or description")
    settings = Settings.load()
    try:
        units = fetch_units(settings)
        return resolve(
            ticket, units, settings,
            lambda text: embed_texts(settings, [text])[0],
            lambda s, u: complete_json(settings, s, u),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    except PsycopgError as exc:
        raise HTTPException(503, f"Database query failed: {exc}") from exc

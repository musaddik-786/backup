from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from psycopg import Error as PsycopgError

from .database import fetch_eligible_tickets
from .pipeline import retrieve
from .providers import Settings, complete_json, embed_texts


class TicketRequest(BaseModel):
    id: str | None = None
    created: str | None = None
    short_description: str | None = None
    description: str | None = None
    system: str | None = None
    severity: str | None = None
    reporter_notes: str | None = None
    ticket_type: str | None = None


app = FastAPI(title="Similar Ticket Retrieval Agent", version="1.0.0")


@app.post("/retrieve")
def retrieve_ticket(request: TicketRequest) -> dict[str, Any]:
    ticket = request.model_dump(exclude_none=True)
    if not any(ticket.get(field) for field in ("short_description", "description", "reporter_notes")):
        raise HTTPException(status_code=422, detail="Provide description, short_description, or reporter_notes")
    settings = Settings.load()
    try:
        records = fetch_eligible_tickets(settings)
        return retrieve(
            ticket,
            records,
            settings,
            lambda text: embed_texts(settings, [text])[0],
            lambda system_prompt, user_prompt: complete_json(settings, system_prompt, user_prompt),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except PsycopgError as exc:
        raise HTTPException(status_code=503, detail=f"Database query failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("retrieval_agent.main:app", host="127.0.0.1", port=8000, reload=False)

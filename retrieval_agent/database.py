"""Read-only access to the knowledge base's ticket_records table: the single ticket store.

The retrieval agent keeps no ticket table of its own. Candidates are the KB tickets whose
curation_status is AutoApproved/SMEApproved and that already have an embedding (written by
backend/step4_store_embeddings.py).
"""
from __future__ import annotations

import json
import math
from typing import Any

from .providers import Settings, EMBEDDING_DIMENSIONS

APPROVED_STATUSES = ("AutoApproved", "SMEApproved")

QUERY = """
    SELECT source_ref_id, title, raw_text, module, severity, category, resolution_type,
           resolution, event_date, source_metadata, embedding::text
    FROM ticket_records
    WHERE embedding IS NOT NULL AND curation_status = ANY(%s)
      AND COALESCE(source_metadata ->> 'removed', 'false') <> 'true'
    ORDER BY source_ref_id
"""


def _vector_text(vector: list[float]) -> str:
    if len(vector) != EMBEDDING_DIMENSIONS or any(not math.isfinite(value) for value in vector):
        raise ValueError(f"Embedding must contain exactly {EMBEDDING_DIMENSIONS} finite values")
    return "[" + ",".join(repr(value) for value in vector) + "]"


def _parse_vector(value: str | None) -> list[float] | None:
    if value is None:
        return None
    try:
        vector = [float(item) for item in json.loads(value)]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Database returned an invalid pgvector value") from exc
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"Stored embedding has {len(vector)} dimensions, expected {EMBEDDING_DIMENSIONS}")
    return vector


def to_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """Map a ticket_records row to the record shape the ranking pipeline expects."""
    meta = row.get("source_metadata")
    meta = json.loads(meta) if isinstance(meta, str) else dict(meta or {})
    meta.setdefault("summary", row.get("title"))
    return {
        "ticket_id": row["source_ref_id"],
        "description": row.get("raw_text"),
        "system": row.get("module"),
        "severity": row.get("severity"),
        "ticket_type": row.get("category"),
        "fix_path": row.get("resolution_type"),
        "resolution": row.get("resolution"),
        "rca": None,
        "runbook_link": None,
        "created": meta.get("created_at") or (str(row["event_date"]) if row.get("event_date") else None),
        "closed": meta.get("closed_at"),
        "source_metadata": meta,
        "embedding": _parse_vector(row.get("embedding")),
        "retrieval_eligible": True,
    }


def fetch_eligible_tickets(settings: Settings) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(settings.database_conninfo(), row_factory=dict_row) as connection:
        rows = connection.execute(QUERY, (list(APPROVED_STATUSES),)).fetchall()
    return [to_candidate(row) for row in rows]

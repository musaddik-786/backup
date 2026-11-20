"""Match a ticket to a KB "Resolution Unit" (one article + its runbook play), or abstain.

Flow: rank the units by keyword (BM25) + meaning (cosine) -> RRF -> top candidates -> ONE LLM call that
either confirms a candidate or says "none" and lists which L1 checklist items the ticket is missing.
The LLM only *selects*; every instruction returned is the stored card text, verbatim.

The embedding and LLM calls are injected, so the whole thing is testable without Azure or a database.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .pipeline import _cosine, bm25_scores
from .providers import EMBEDDING_DIMENSIONS, Settings

SYSTEM_PROMPT = """You match a new Guidewire support ticket to a knowledge-base card, or decide that none applies.
You are given candidate cards (title, keywords, symptoms, cause) and the ticket text.
Rules:
- Choose a card ONLY if the ticket describes the same failure mechanism as that card's symptoms/cause.
  Sharing a product area (e.g. 'audit' or 'endorsement') is not enough.
- Tickets are short and often do not name the line of business; do not require it.
- If no candidate fits, return unit_id null. Do not force a match.
- Only use unit_ids from the candidates.
- missing_info: the 1-based numbers of the card's 'information to collect' items that the ticket does NOT
  already provide. Return [] when unit_id is null.
Return JSON only: {"unit_id": string|null, "confidence": "high"|"medium"|"low", "reason": string (one sentence), "missing_info": [int]}"""

QUERY = """
    SELECT unit_id, article_id, title, match_text, content, version, review_status, kb_version, embedding::text
    FROM kb_units
    ORDER BY unit_id
"""


def fetch_units(settings: Settings) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(settings.database_conninfo(), row_factory=dict_row) as connection:
        rows = connection.execute(QUERY).fetchall()
    units = []
    for row in rows:
        content = row["content"]
        content = json.loads(content) if isinstance(content, str) else content
        vector = [float(x) for x in json.loads(row["embedding"])] if row.get("embedding") else None
        if vector is not None and len(vector) != EMBEDDING_DIMENSIONS:
            raise ValueError(f"{row['unit_id']}: stored embedding has {len(vector)} dims, expected {EMBEDDING_DIMENSIONS}")
        units.append({**{k: row[k] for k in ("unit_id", "article_id", "title", "match_text", "version",
                                              "review_status", "kb_version")},
                      "content": content, "embedding": vector})
    return units


def ticket_text(ticket: dict[str, Any]) -> str:
    return "\n".join(str(ticket[k]) for k in ("title", "description") if ticket.get(k)).strip()


def rank_units(query: str, units: list[dict[str, Any]], query_embedding: list[float] | None,
               rrf_constant: int = 60, limit: int = 5) -> list[dict[str, Any]]:
    """Fuse keyword and meaning rankings (reciprocal rank fusion). Returns the top `limit` units with scores."""
    lexical = bm25_scores(query, [u["match_text"] for u in units])
    semantic = [
        _cosine(query_embedding, u["embedding"]) if query_embedding and u.get("embedding") else 0.0
        for u in units
    ]
    lex_order = sorted(range(len(units)), key=lambda i: lexical[i], reverse=True)
    sem_order = sorted(range(len(units)), key=lambda i: semantic[i], reverse=True)
    lex_rank = {i: r + 1 for r, i in enumerate(lex_order) if lexical[i] > 0}
    sem_rank = {i: r + 1 for r, i in enumerate(sem_order)} if query_embedding else {}
    k = max(1, rrf_constant)
    ranked = []
    for i, unit in enumerate(units):
        fused = (1 / (k + lex_rank[i]) if i in lex_rank else 0.0) + (1 / (k + sem_rank[i]) if i in sem_rank else 0.0)
        ranked.append({"unit": unit, "rrf": round(fused, 6), "bm25": round(lexical[i], 3), "cosine": round(semantic[i], 4)})
    ranked.sort(key=lambda r: (r["rrf"], r["cosine"]), reverse=True)
    return ranked[:limit]


def _candidate_block(n: int, unit: dict[str, Any]) -> str:
    c = unit["content"]
    return (f"[{n}] unit_id={unit['unit_id']}\nTitle: {c['title']}\nKeywords: {'; '.join(c['keywords'])}\n"
            f"Symptoms: {'; '.join(c['symptoms'])}\nCause: {c['cause']}\n"
            f"Information to collect: " + " | ".join(f"{i}. {t}" for i, t in enumerate(c["info_to_collect"], 1)))


def _validate(completion: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(completion, dict):
        raise RuntimeError("KB match response was not a JSON object")
    unit_id = completion.get("unit_id")
    allowed = {c["unit"]["unit_id"] for c in candidates}
    if unit_id is not None and unit_id not in allowed:
        raise RuntimeError("KB match referenced a card outside the candidates")
    confidence = completion.get("confidence")
    if confidence not in {"high", "medium", "low"}:
        raise RuntimeError("KB match returned an unsupported confidence")
    missing = completion.get("missing_info") or []
    if not isinstance(missing, list) or any(not isinstance(i, int) for i in missing):
        raise RuntimeError("KB match returned malformed missing_info")
    return {"unit_id": unit_id, "confidence": confidence,
            "reason": str(completion.get("reason") or "not recorded"), "missing_info": missing}


def _candidate_summary(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"unit_id": r["unit"]["unit_id"], "title": r["unit"]["title"], "rrf": r["rrf"],
             "bm25": r["bm25"], "cosine": r["cosine"]} for r in ranked]


def resolve(ticket: dict[str, Any], units: list[dict[str, Any]], settings: Settings,
            embed: Callable[[str], list[float]], complete: Callable[[str, str], dict]) -> dict[str, Any]:
    """Return {matched, unit, confidence, reason, missing_info, candidates, verified}."""
    text = ticket_text(ticket)
    if not text:
        raise ValueError("Provide a title or description")
    if not units:
        raise RuntimeError("The knowledge base has no units loaded (run scripts/kb_load.py)")
    ranked = rank_units(text, units, embed(text), settings.rrf_constant)
    base = {"matched": False, "unit": None, "confidence": None, "reason": "", "missing_info": [],
            "candidates": _candidate_summary(ranked), "verified": True}
    if not ranked or (ranked[0]["bm25"] == 0 and ranked[0]["cosine"] <= 0):
        return {**base, "reason": "Nothing in the knowledge base resembles this ticket."}

    prompt = "Candidate cards:\n" + "\n\n".join(_candidate_block(i, r["unit"]) for i, r in enumerate(ranked, 1)) \
             + f"\n\nTicket:\n{text}"
    try:
        verdict = _validate(complete(SYSTEM_PROMPT, prompt), ranked)
    except RuntimeError as exc:
        # Azure down / bad response: show the best keyword+meaning candidate, clearly marked unverified.
        top = ranked[0]["unit"]
        return {**base, "matched": True, "unit": _public(top), "confidence": "low", "verified": False,
                "reason": f"Automatic confirmation unavailable ({exc}); showing the closest card."}

    if verdict["unit_id"] is None:
        return {**base, "reason": verdict["reason"]}
    unit = next(r["unit"] for r in ranked if r["unit"]["unit_id"] == verdict["unit_id"])
    items = unit["content"]["info_to_collect"]
    missing = [items[i - 1] for i in verdict["missing_info"] if 1 <= i <= len(items)]
    return {**base, "matched": True, "unit": _public(unit), "confidence": verdict["confidence"],
            "reason": verdict["reason"], "missing_info": missing}


def _public(unit: dict[str, Any]) -> dict[str, Any]:
    """The card as shown to agents: verbatim stored content plus its id/version for citation."""
    return {"unit_id": unit["unit_id"], "article_id": unit["article_id"], "version": unit["version"],
            "kb_version": unit.get("kb_version"), "review_status": unit.get("review_status"),
            "content": unit["content"]}

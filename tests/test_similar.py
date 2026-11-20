import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError
from app import main
from app.routers import similar

REC = [{"ticket_id": "INC-1", "description": "Bind fails", "system": "PolicyCenter", "created": "2025-01-01",
        "resolution": "Patch rule", "rca": None, "source_metadata": {"assignee_team": "PC L2"}, "embedding": [1.0]},
       {"ticket_id": "INC-9", "description": "self", "system": "X", "source_metadata": {}, "embedding": [1.0]}]

@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(similar.Settings, "load", classmethod(lambda cls: object()))
    monkeypatch.setattr(similar, "fetch_eligible_tickets", lambda s: list(REC))
    return TestClient(main.app)

def fake_retrieve(result):
    def f(ticket, records, *a):
        f.seen = [r["ticket_id"] for r in records]
        return result
    return f

def test_matches_are_enriched_and_self_excluded(c, monkeypatch):
    fr = fake_retrieve({"matches": [{"id": "INC-1", "rank": 1, "match_strength": "Strong"}],
                        "no_confident_match": False, "suggested_next_step": "x"})
    monkeypatch.setattr(similar, "retrieve", fr)
    r = c.post("/api/tickets/similar", json={"id": "INC-9", "title": "Bind error"})
    assert r.status_code == 200
    assert fr.seen == ["INC-1"]
    t = r.json()["matches"][0]["ticket"]
    assert t["resolution"] == "Patch rule" and t["assignee_team"] == "PC L2" and t["system"] == "PolicyCenter"

def test_no_confident_match_passthrough(c, monkeypatch):
    monkeypatch.setattr(similar, "retrieve", fake_retrieve({"matches": [], "no_confident_match": True, "suggested_next_step": "escalate"}))
    j = c.post("/api/tickets/similar", json={"description": "odd"}).json()
    assert j["no_confident_match"] and j["matches"] == []

def test_requires_text(c):
    assert c.post("/api/tickets/similar", json={"id": "T"}).status_code == 422

@pytest.mark.parametrize("exc,code", [(ValueError("bad"), 422), (RuntimeError("llm"), 502), (OperationalError("db"), 503)])
def test_errors_map_to_status(c, monkeypatch, exc, code):
    def boom(*a): raise exc
    monkeypatch.setattr(similar, "retrieve", boom)
    assert c.post("/api/tickets/similar", json={"title": "x"}).status_code == code

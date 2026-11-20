import json
from fastapi.testclient import TestClient
from app import main
from app.routers import tickets

class FakeConn:
    def __init__(self, rows): self.rows = rows
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, q): return self
    def fetchall(self): return self.rows

def client(monkeypatch, rows):
    monkeypatch.setattr(tickets, "get_conn", lambda: FakeConn(rows))
    return TestClient(main.app)

def test_health(monkeypatch):
    assert client(monkeypatch, []).get("/health").json() == {"status": "ok"}

def test_tickets_and_modules(monkeypatch):
    c = client(monkeypatch, [{"module": "PolicyCenter", "record_id": "r1"}])
    assert c.get("/api/tickets").json()[0]["record_id"] == "r1"
    assert c.get("/api/modules").json() == ["PolicyCenter"]

def test_db_failure_returns_500(monkeypatch):
    def boom(): raise RuntimeError("down")
    monkeypatch.setattr(tickets, "get_conn", boom)
    assert TestClient(main.app).get("/api/tickets").status_code == 500

def test_assignee_teams_from_raw_json(monkeypatch):
    r = client(monkeypatch, []).get("/api/assignee-teams")
    assert r.status_code == 200 and r.json() == sorted(set(r.json())) and len(r.json()) > 0

def test_retrieve_validation(monkeypatch):
    assert client(monkeypatch, []).post("/retrieve", json={"id": "X"}).status_code == 422

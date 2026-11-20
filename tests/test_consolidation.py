import json, re, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def load(p): return json.loads((ROOT / p).read_text(encoding="utf-8"))

def test_no_data_loss_tickets_and_release_notes():
    for f, k in [("tickets", "ticket_id"), ("release_notes", "release_id")]:
        merged = {r[k] for r in load(f"data/raw/{f}.json")}
        assert {r[k] for r in load(f"data/archive/kavita/{f}.json")} <= merged
    assert len(load("data/raw/tickets.json")) == 74

def test_embedding_dimension_is_single_env_setting_defaulting_to_3072():
    for p in ["backend/structuring.py", "retrieval_agent/providers.py"]:
        t = (ROOT / p).read_text(encoding="utf-8")
        assert 'os.environ.get("EMBEDDING_DIMENSIONS", "3072")' in t, p
    assert "dimensions=EMBEDDING_DIM" in (ROOT / "backend/structuring.py").read_text()

def test_new_db_script_refuses_same_name_and_never_alters():
    s = (ROOT / "db/create_new_db.py").read_text()
    assert "name == base" in s and "ALTER" not in s and "DROP" not in s

def test_secrets_ignored_and_not_tracked():
    assert subprocess.run(["git", "check-ignore", ".env"], cwd=ROOT).returncode == 0
    assert subprocess.run(["git", "check-ignore", "ui/node_modules"], cwd=ROOT).returncode == 0
    out = subprocess.run(["git", "ls-files", "-o", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True).stdout
    assert not re.search(r"(^|/)(\.env|venv|node_modules|__pycache__)(/|$)|\.pyc$", out, re.M)

def test_backend_paths_resolve_to_root():
    import ingestion
    assert ingestion.DATA_DIR == ROOT / "data" / "raw" and ingestion.DATA_DIR.is_dir()
    assert (ROOT / "db" / "schema.sql").is_file()

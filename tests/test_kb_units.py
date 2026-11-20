import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import kb_load  # noqa: E402


def test_thirteen_units_validate():
    units = kb_load.load_units()
    assert len(units) == 13
    codes = {c for u in units for c in u["root_cause_codes"]}
    assert {"OOS-01", "OOS-02", "OOS-03", "OOS-04", "REL-05"} <= codes
    assert {f"AUD-0{i}" for i in range(1, 7)} <= codes
    assert {f"PRM-0{i}" for i in range(1, 4)} <= codes


def test_match_text_describes_problem_not_fix():
    unit = next(u for u in kb_load.load_units() if u["unit_id"] == "PC-AUD-01")
    text = kb_load.match_text(unit)
    assert "premium decreases" in text.lower()
    assert unit["resolution"][0] not in text


def test_content_hash_is_stable_and_sensitive():
    unit = dict(kb_load.load_units()[0])
    h = kb_load.content_hash(unit)
    assert h == kb_load.content_hash(dict(unit))
    unit["escalation"] += " changed"
    assert h != kb_load.content_hash(unit)

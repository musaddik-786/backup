"""Load knowledge/units/*.json into the kb_units table.

    python scripts/kb_load.py --dry-run     # validate the JSON files only, no DB or Azure calls
    python scripts/kb_load.py               # apply migration, embed, upsert (idempotent)

Re-running changes nothing: a card is re-embedded only when its content hash changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS_DIR = ROOT / "knowledge" / "units"
MIGRATION = ROOT / "db" / "migrations" / "2026-11-19_kb_units.sql"

REQUIRED = [
    "unit_id", "article_id", "root_cause_codes", "runbook", "play", "module", "category", "title",
    "keywords", "symptoms", "cause", "info_to_collect", "diagnosis", "resolution", "workaround",
    "permanent_fix", "escalation", "related_tickets", "version",
]


def load_units(units_dir: Path = UNITS_DIR) -> list[dict]:
    units = []
    for path in sorted(units_dir.glob("*.json")):
        unit = json.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED if k not in unit or unit[k] in (None, "", [])]
        # workaround may legitimately be empty text only if explicitly "None"; related tickets may be empty
        missing = [k for k in missing if k != "related_tickets"]
        if missing:
            raise ValueError(f"{path.name}: missing/empty fields {missing}")
        if unit["unit_id"] != path.stem:
            raise ValueError(f"{path.name}: unit_id {unit['unit_id']!r} does not match filename")
        units.append(unit)
    ids = [u["unit_id"] for u in units]
    arts = [u["article_id"] for u in units]
    if len(set(ids)) != len(ids) or len(set(arts)) != len(arts):
        raise ValueError("duplicate unit_id or article_id")
    return units


def match_text(unit: dict) -> str:
    """Text that describes what the problem LOOKS LIKE (never the fix), used for matching tickets."""
    return "\n".join([
        unit["title"],
        "Keywords: " + "; ".join(unit["keywords"]),
        "Symptoms: " + "; ".join(unit["symptoms"]),
        "Cause: " + unit["cause"],
    ])


def content_hash(unit: dict) -> str:
    return hashlib.sha256(json.dumps(unit, sort_keys=True).encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="validate files only")
    args = ap.parse_args()

    units = load_units()
    print(f"Validated {len(units)} units: {', '.join(u['unit_id'] for u in units)}")
    if args.dry_run:
        return 0

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "backend"))  # backend modules import siblings as top-level names
    from dotenv import load_dotenv  # noqa: E402

    load_dotenv(ROOT / ".env")
    from knowledge_store import get_connection, new_kb_version  # noqa: E402
    from retrieval_agent.providers import Settings, embed_texts  # noqa: E402

    settings = Settings.load()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(MIGRATION.read_text(encoding="utf-8"))
            cur.execute("SELECT unit_id, content_hash FROM kb_units")
            existing = dict(cur.fetchall())
        changed = [u for u in units if existing.get(u["unit_id"]) != content_hash(u)]
        print(f"{len(changed)} new/changed, {len(units) - len(changed)} unchanged")
        if changed:
            vectors = embed_texts(settings, [match_text(u) for u in changed])
            kb_version = None
            try:
                kb_version = new_kb_version()
            except Exception:  # kb_version is informational here; don't block the load
                pass
            with conn.cursor() as cur:
                for unit, vec in zip(changed, vectors):
                    cur.execute(
                        """
                        INSERT INTO kb_units (unit_id, article_id, root_cause_codes, runbook, play, module,
                            category_path, title, match_text, content, version, review_status,
                            content_hash, kb_version, embedding)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::vector)
                        ON CONFLICT (unit_id) DO UPDATE SET
                            article_id=EXCLUDED.article_id, root_cause_codes=EXCLUDED.root_cause_codes,
                            runbook=EXCLUDED.runbook, play=EXCLUDED.play, module=EXCLUDED.module,
                            category_path=EXCLUDED.category_path, title=EXCLUDED.title,
                            match_text=EXCLUDED.match_text, content=EXCLUDED.content,
                            version=EXCLUDED.version, review_status=EXCLUDED.review_status,
                            content_hash=EXCLUDED.content_hash, kb_version=EXCLUDED.kb_version,
                            embedding=EXCLUDED.embedding, loaded_at=now()
                        """,
                        (unit["unit_id"], unit["article_id"], unit["root_cause_codes"], unit["runbook"],
                         unit["play"], unit["module"], unit["category"], unit["title"], match_text(unit),
                         json.dumps(unit), unit["version"], unit.get("review_status", "draft-pending-sme-review"),
                         content_hash(unit), kb_version, json.dumps(vec)),
                    )
        conn.commit()
        print("Done.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

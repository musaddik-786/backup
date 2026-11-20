"""Evaluate the KB resolver on the historical-tickets Excel.

    python -m retrieval_agent.kb_eval PATH.xlsx [--set-b-sample 40] [--out results.json]

Set A = tickets cited by a KB card (expected answer known). NOTE: the cards were derived from these tickets,
so Set A is a smoke test, not an honest accuracy figure.
Set B = tickets no card cites (a random sample). There is no label, so we report how many get a match and
print them for a human to judge.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

import openpyxl

from .kb_resolver import fetch_units, resolve
from .providers import Settings, complete_json, embed_texts

UNITS_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "units"


def load_tickets(path: str) -> list[dict]:
    ws = openpyxl.load_workbook(path, read_only=True)["Scope"]
    rows = list(ws.iter_rows(values_only=True))
    ix = {name: i for i, name in enumerate(rows[0])}
    return [{"id": r[0], "description": str(r[ix["High-level description"]] or ""),
             "system": r[ix["System (Client reading)"]]} for r in rows[1:] if r[0]]


def expected_by_ticket() -> dict[str, str]:
    expected: dict[str, str] = {}
    for f in sorted(UNITS_DIR.glob("*.json")):
        unit = json.loads(f.read_text(encoding="utf-8"))
        for t in unit["related_tickets"]:
            expected.setdefault(t, unit["unit_id"])
    return expected


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--set-b-sample", type=int, default=40)
    ap.add_argument("--out")
    args = ap.parse_args()

    settings = Settings.load()
    units = fetch_units(settings)
    embed = lambda text: embed_texts(settings, [text])[0]  # noqa: E731
    complete = lambda s, u: complete_json(settings, s, u)  # noqa: E731

    tickets = load_tickets(args.xlsx)
    expected = expected_by_ticket()
    set_a = [t for t in tickets if t["id"] in expected]
    rest = [t for t in tickets if t["id"] not in expected]
    random.Random(7).shuffle(rest)
    set_b = rest[: args.set_b_sample]

    results = {"A": [], "B": []}
    for label, group in (("A", set_a), ("B", set_b)):
        for t in group:
            out = resolve({"description": t["description"]}, units, settings, embed, complete)
            got = out["unit"]["unit_id"] if out["matched"] else None
            top3 = [c["unit_id"] for c in out["candidates"][:3]]
            results[label].append({"id": t["id"], "description": t["description"], "system": t["system"],
                                   "expected": expected.get(t["id"]), "got": got, "top3": top3,
                                   "verified": out["verified"], "confidence": out["confidence"], "reason": out["reason"]})

    per = defaultdict(lambda: [0, 0, 0])  # total, top1 correct, in candidates top3
    for r in results["A"]:
        per[r["expected"]][0] += 1
        per[r["expected"]][1] += r["got"] == r["expected"]
        per[r["expected"]][2] += r["expected"] in r["top3"]
    print("SET A (smoke test; cards were built from these tickets)")
    for unit_id in sorted(per):
        total, top1, top3 = per[unit_id]
        print(f"  {unit_id:10} n={total:2}  confirmed-correct {top1}/{total}   in-top3-candidates {top3}/{total}")
    a_total = len(results["A"])
    print(f"  ALL        n={a_total}  confirmed-correct {sum(p[1] for p in per.values())}/{a_total}"
          f"   in-top3 {sum(p[2] for p in per.values())}/{a_total}")
    matched_b = [r for r in results["B"] if r["got"]]
    print(f"\nSET B (n={len(results['B'])}, uncited tickets): matched a card {len(matched_b)}, no match {len(results['B']) - len(matched_b)}")
    for r in results["B"]:
        print(f"  {r['id']} [{r['system']}] -> {r['got'] or 'NO MATCH':10} | {r['description'][:80]}")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

"""Seeded 70/30 stratified (by module_reported) split of tickets -> data/split/{train,test}/.
Other sources (chat_rca, release_notes, runbooks) are copied whole into train as evidence."""
import json, random, shutil
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; RAW = ROOT/"data"/"raw"; OUT = ROOT/"data"/"split"
tickets = json.loads((RAW/"tickets.json").read_text(encoding="utf-8"))
by = defaultdict(list)
for t in tickets: by[t.get("module_reported") or "?"].append(t)
rnd = random.Random(42); train, test = [], []
for _, g in sorted(by.items()):
    rnd.shuffle(g); n = max(1, round(len(g)*0.7)) if len(g) > 1 else 1
    train += g[:n]; test += g[n:]
for d in ("train","test"): (OUT/d).mkdir(parents=True, exist_ok=True)
for f in ("chat_rca","release_notes","runbooks"): shutil.copy(RAW/f"{f}.json", OUT/"train"/f"{f}.json")
(OUT/"train"/"tickets.json").write_text(json.dumps(train, indent=2, ensure_ascii=False), encoding="utf-8")
(OUT/"test"/"tickets.json").write_text(json.dumps(test, indent=2, ensure_ascii=False), encoding="utf-8")
print(len(train), "train /", len(test), "test of", len(tickets))

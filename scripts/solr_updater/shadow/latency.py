"""For each churned edit, how long until each follower picked up its keys.

usage: python latency.py <out dir>
"""

import json
import statistics
import sys
from pathlib import Path

out = Path(sys.argv[1])
ops = [json.loads(line) for line in (out / "churn.jsonl").open()]
ops = [o for o in ops if "keys" in o]


def polls(source):
    p = out / f"{source}-keys.jsonl"
    return [json.loads(line) for line in p.open()] if p.exists() else []


result = {}
for source in ("infobase", "db"):
    ps = polls(source)
    lat = []
    missed = 0
    for op in ops:
        # the edited doc itself is always the first key churn reports (lists/books/works/authors)
        key = op["keys"][0] if op["keys"] else None
        if not key or not key.startswith(("/works/", "/books/", "/authors/", "/people/")):
            continue
        hit = next((p["t"] for p in ps if p["t"] >= op["t"] - 10 and key in p["keys"]), None)
        if hit is None:
            missed += 1
        else:
            lat.append(max(0.0, hit - op["t"]))
    lat.sort()
    result[source] = {
        "edits": len(lat) + missed,
        "not_seen": missed,
        "median_s": round(statistics.median(lat), 2) if lat else None,
        "p95_s": round(lat[int(len(lat) * 0.95) - 1], 2) if lat else None,
        "max_s": round(lat[-1], 2) if lat else None,
    }
print(json.dumps(result, indent=1))
(out / "latency.json").write_text(json.dumps(result, indent=1))

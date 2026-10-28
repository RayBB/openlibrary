# Reference-only test harness; lint rules relaxed.
# ruff: noqa: ASYNC230, ASYNC240, PLC0415, SIM115
"""Shadow-test harness: run the infobase-log updater and the database updater
side by side, each into its own Solr core, and compare them with each other and
with a from-scratch reindex.

Runs inside the solr-updater container (repo at /openlibrary, this dir at /shadow):

  python /shadow/shadow.py setup                 # fresh cores + identical baseline index
  python /shadow/shadow.py follow --source db    # follow changes into shadow_db (until /shadow/out/stop exists)
  python /shadow/shadow.py follow --source infobase
  python /shadow/shadow.py truth                 # fresh full reindex into shadow_truth
  python /shadow/shadow.py compare               # writes /shadow/out/report.json
"""

import argparse
import asyncio
import collections
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, "/openlibrary")

from openlibrary.core import async_db
from openlibrary.solr import update
from openlibrary.solr.utils import set_solr_base_url
from scripts.solr_updater import solr_updater

OL_CONFIG = "conf/openlibrary.yml"
OL_URL = "http://web:8080/"
SOLR = "http://solr:8983/solr"
CORES = {"infobase": "shadow_infobase", "db": "shadow_db", "truth": "shadow_truth"}
OUT = Path("/shadow/out")
INDEXED_TYPES = ("/type/work", "/type/edition", "/type/author", "/type/list")
# fields that depend on when a doc was indexed, not on the data
VOLATILE_FIELDS = {"_version_", "_root_"}

logger = logging.getLogger("shadow")


def init():
    os.chdir("/openlibrary")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    update.load_configs(OL_URL, OL_CONFIG, "default")
    solr_updater.args = {"ol_url": OL_URL, "ol_config": OL_CONFIG}
    OUT.mkdir(exist_ok=True)


def use_core(name: str):
    set_solr_base_url(f"{SOLR}/{CORES[name]}")


def solr_admin(**params):
    r = httpx.get(f"{SOLR}/admin/cores", params={"wt": "json", **params}, timeout=120)
    return r.json()


def recreate_core(name: str):
    core = CORES[name]
    # The instance dir with its conf/ is copied in by run.sh (solr's configset dir isn't writable)
    solr_admin(action="UNLOAD", core=core, deleteIndex="true", deleteDataDir="true")
    res = solr_admin(action="CREATE", name=core, instanceDir=core)
    if "error" in res:
        raise RuntimeError(res)
    logger.info("created core %s", core)


def commit(name: str):
    httpx.get(f"{SOLR}/{CORES[name]}/update", params={"commit": "true"}, timeout=120).raise_for_status()


async def all_indexable_keys() -> list[str]:
    rows = await async_db.fetch_all(
        """
        SELECT thing.key FROM thing JOIN thing type ON type.id = thing.type
        WHERE type.key = ANY(%(types)s) ORDER BY thing.id
        """,
        {"types": list(INDEXED_TYPES)},
    )
    return [r["key"] for r in rows]


async def full_reindex(name: str):
    use_core(name)
    keys = await all_indexable_keys()
    logger.info("full reindex of %d keys into %s", len(keys), CORES[name])
    await solr_updater.update_keys(keys)
    commit(name)


async def db_offset_now() -> str:
    tx = await async_db.fetch_val("SELECT coalesce(max(id), 0) FROM transaction")
    store = await async_db.fetch_val("SELECT coalesce(max(id), 0) FROM store")
    return f"db:{tx}:{store}"


def infobase_offset_now() -> str:
    from infogami import config

    log = solr_updater.InfobaseLog(config.get("infobase_server"))
    log.seek(solr_updater.get_default_offset())
    while list(log.read_records()):
        pass
    return log.tell()


async def cmd_setup(_):
    for name in CORES:
        recreate_core(name)
    # one baseline index, copied into both shadow cores so they start identical
    await full_reindex("infobase")
    await full_reindex("db")
    offsets = {"infobase": infobase_offset_now(), "db": await db_offset_now()}
    (OUT / "start_offsets.json").write_text(json.dumps(offsets))
    for f in [*OUT.glob("*-keys.jsonl"), *OUT.glob("*.state")]:
        f.unlink()
    (OUT / "stop").unlink(missing_ok=True)
    logger.info("start offsets %s", offsets)


async def cmd_follow(a):
    """Mirror of solr_updater.main's loop, recording what it indexes."""
    use_core(a.source)
    # resume like the real updater does after a restart
    state_file = OUT / f"{a.source}.state"
    offset = state_file.read_text() if state_file.exists() else json.loads((OUT / "start_offsets.json").read_text())[a.source]
    logger.info("%s follower starting at %s", a.source, offset)
    if a.source == "db":
        log = solr_updater.DatabaseLog(settle_seconds=a.settle_seconds)
    else:
        from infogami import config

        log = solr_updater.InfobaseLog(config.get("infobase_server"))
    log.seek(offset)

    keys_out = open(OUT / f"{a.source}-keys.jsonl", "a")
    idle_since = None
    while True:
        if isinstance(log, solr_updater.DatabaseLog):
            records = await log.read_records()
        else:
            records = list(log.read_records())
        keys = [k for k in solr_updater.parse_log(records, False) if update.can_update_key(k)]
        if keys:
            keys_out.write(json.dumps({"t": time.time(), "offset": log.tell(), "records": len(records), "keys": keys}) + "\n")
            keys_out.flush()
        count = await solr_updater.update_keys(keys)
        state_file.write_text(log.tell())
        if count:
            idle_since = None
        else:
            idle_since = idle_since or time.time()
            if (OUT / "stop").exists() and time.time() - idle_since > a.idle_exit:
                break
            await asyncio.sleep(a.poll)
    commit(a.source)
    logger.info("%s follower done at offset %s", a.source, log.tell())


async def cmd_truth(_):
    recreate_core("truth")
    # Second pass: author/list docs aggregate over work docs already in Solr
    await full_reindex("truth")
    await full_reindex("truth")


def fetch_core(name: str) -> dict[str, dict]:
    commit(name)
    docs, cursor = {}, "*"
    while True:
        r = httpx.get(
            f"{SOLR}/{CORES[name]}/select",
            params={"q": "*:*", "fl": "*", "rows": 500, "sort": "key asc", "cursorMark": cursor, "wt": "json"},
            timeout=120,
        ).json()
        for d in r["response"]["docs"]:
            docs[d["key"]] = {k: v for k, v in d.items() if k not in VOLATILE_FIELDS}
        if r["nextCursorMark"] == cursor:
            return docs
        cursor = r["nextCursorMark"]


# Fields computed by querying Solr (or derived from other docs) at index time, so they
# depend on what else was already indexed/committed, not only on the doc's own data.
SOLR_DERIVED = {
    "/authors/": {"work_count", "top_work", "top_subjects"},
    "/lists/": {f"{kind}{suffix}" for kind in ("subject", "person", "place", "time") for suffix in ("", "_facet", "_key")},
}


def normalize(doc: dict) -> dict:
    """Multi-valued fields come out in set-iteration order, which varies per process."""
    out = {}
    for f, v in doc.items():
        if isinstance(v, list):
            v = sorted(v, key=lambda x: json.dumps(x, sort_keys=True))
        out[f] = v
    # *_sort fields pick one value out of a set; equal if both picks are valid values
    for f in [f for f in out if f.endswith("_sort")]:
        base = out.get(f.removesuffix("_sort"))
        if isinstance(base, list) and out[f] in base:
            out[f] = "<one of %s>" % f.removesuffix("_sort")
    return out


# Fetched live from archive.org item metadata at index time
EXTERNAL = {"ia_collection", "ia_collection_s", "ia_box_id", "lending_edition_s", "lending_identifier_s"}


def field_category(key: str, field: str) -> str:
    if field in EXTERNAL:
        return "external"
    for marker, fields in SOLR_DERIVED.items():
        if marker in key and field in fields:
            return "solr_derived"
    return "data"


def diff_cores(a: dict[str, dict], b: dict[str, dict]) -> dict:
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    changed: dict[str, dict] = {}
    raw_order_only = 0
    for key in sorted(set(a) & set(b)):
        if a[key] == b[key]:
            continue
        na, nb = normalize(a[key]), normalize(b[key])
        if na == nb:
            raw_order_only += 1
            continue
        fields = sorted(f for f in set(na) | set(nb) if na.get(f) != nb.get(f))
        changed[key] = {f: {"category": field_category(key, f), "left": na.get(f), "right": nb.get(f)} for f in fields}
    data_keys = sorted(k for k, fs in changed.items() if any(v["category"] == "data" for v in fs.values()))
    return {
        "only_left": only_a,
        "only_right": only_b,
        "changed": changed,
        "changed_data_keys": data_keys,
        "order_only": raw_order_only,
        "left_count": len(a),
        "right_count": len(b),
    }


def read_keys(source: str):
    path = OUT / f"{source}-keys.jsonl"
    polls = [json.loads(line) for line in path.open()] if path.exists() else []
    counter = collections.Counter(k for p in polls for k in p["keys"])
    return polls, counter


async def cmd_compare(_):
    cores = {name: fetch_core(name) for name in CORES}
    ib_polls, ib_keys = read_keys("infobase")
    db_polls, db_keys = read_keys("db")
    report = {
        "doc_counts": {name: len(docs) for name, docs in cores.items()},
        "infobase_vs_truth": diff_cores(cores["infobase"], cores["truth"]),
        "db_vs_truth": diff_cores(cores["db"], cores["truth"]),
        "infobase_vs_db": diff_cores(cores["infobase"], cores["db"]),
        "keys": {
            "infobase_unique": len(ib_keys),
            "db_unique": len(db_keys),
            "infobase_total": sum(ib_keys.values()),
            "db_total": sum(db_keys.values()),
            "only_infobase": sorted(set(ib_keys) - set(db_keys)),
            "only_db": sorted(set(db_keys) - set(ib_keys)),
            "infobase_polls": len(ib_polls),
            "db_polls": len(db_polls),
        },
    }
    # Which source is closer to the truth, key by key (data fields only)
    ib_wrong = (
        set(report["infobase_vs_truth"]["changed_data_keys"]) | set(report["infobase_vs_truth"]["only_left"]) | set(report["infobase_vs_truth"]["only_right"])
    )
    db_wrong = set(report["db_vs_truth"]["changed_data_keys"]) | set(report["db_vs_truth"]["only_left"]) | set(report["db_vs_truth"]["only_right"])
    report["closeness"] = {
        "wrong_in_both": sorted(ib_wrong & db_wrong),
        "wrong_only_in_infobase": sorted(ib_wrong - db_wrong),
        "wrong_only_in_db": sorted(db_wrong - ib_wrong),
    }
    report["summary"] = summary = {
        "doc_counts": report["doc_counts"],
        **{
            f"{k}_diff": {
                "only_left": len(report[k]["only_left"]),
                "only_right": len(report[k]["only_right"]),
                "changed": len(report[k]["changed"]),
                "changed_data": len(report[k]["changed_data_keys"]),
                "order_only": report[k]["order_only"],
            }
            for k in ("infobase_vs_truth", "db_vs_truth", "infobase_vs_db")
        },
        "closeness": {k: len(v) for k, v in report["closeness"].items()},
        "keys": {k: v if isinstance(v, int) else len(v) for k, v in report["keys"].items()},
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(summary, indent=1))


async def run(a):
    init()
    try:
        await {"setup": cmd_setup, "follow": cmd_follow, "truth": cmd_truth, "compare": cmd_compare}[a.cmd](a)
    finally:
        await async_db.close_pool()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["setup", "follow", "truth", "compare"])
    p.add_argument("--source", choices=["infobase", "db"])
    p.add_argument("--poll", type=float, default=1)
    p.add_argument("--idle-exit", type=float, default=20)
    p.add_argument("--settle-seconds", type=float, default=600)
    asyncio.run(run(p.parse_args()))

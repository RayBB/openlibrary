# Reference-only test harness; lint rules relaxed.
# ruff: noqa: ASYNC230, SIM115
"""Replay today's whole history through both readers (no indexing) and compare the keys."""

import asyncio
import collections
import json
import os
import sys
import time

sys.path.insert(0, "/openlibrary")
os.chdir("/openlibrary")
from infogami import config
from openlibrary.config import load_config
from openlibrary.core import async_db
from openlibrary.solr import update
from scripts.solr_updater.solr_updater import DatabaseLog, InfobaseLog, parse_log


async def main():
    load_config("conf/openlibrary.yml")
    update.load_configs("http://web:8080/", "conf/openlibrary.yml", "default")
    day = time.strftime("%Y-%m-%d", time.gmtime())

    ib = InfobaseLog(config.get("infobase_server"))
    ib.seek(f"{day}:0")
    ib_records, t = [], time.time()
    while batch := list(ib.read_records()):
        ib_records += batch
    ib_s = time.time() - t

    db = DatabaseLog()
    start = await DatabaseLog.offset_for_date(day)
    # the store cursor of offset_for_date starts "now"; replay store from 0 to compare everything
    db.seek(start.rsplit(":", 1)[0] + ":0")
    db_records, t = [], time.time()
    while batch := await db.read_records():
        db_records += batch
    db_s = time.time() - t

    def summarize(records):
        keys = [k for k in parse_log(records, False) if update.can_update_key(k)]
        return collections.Counter(r["action"] for r in records), collections.Counter(keys)

    ib_actions, ib_keys = summarize(ib_records)
    db_actions, db_keys = summarize(db_records)
    result = {
        "day": day,
        "db_start_offset": start,
        "db_end_offset": db.tell(),
        "infobase_records": dict(ib_actions),
        "db_records": dict(db_actions),
        "infobase_read_seconds": round(ib_s, 2),
        "db_read_seconds": round(db_s, 2),
        "unique_keys": {"infobase": len(ib_keys), "db": len(db_keys)},
        "key_events": {"infobase": sum(ib_keys.values()), "db": sum(db_keys.values())},
        "only_infobase": sorted(set(ib_keys) - set(db_keys)),
        "only_db": sorted(set(db_keys) - set(ib_keys)),
        "count_differs": {k: [ib_keys[k], db_keys[k]] for k in set(ib_keys) | set(db_keys) if ib_keys[k] != db_keys[k]},
    }
    print(json.dumps(result, indent=1))
    open("/shadow/out/replay_day.json", "w").write(json.dumps(result, indent=1))
    await async_db.close_pool()


asyncio.run(main())

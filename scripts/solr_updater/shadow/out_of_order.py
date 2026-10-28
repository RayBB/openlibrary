"""Against the real local Postgres + infobase: prove DatabaseLog does not lose a
transaction that commits after a higher-numbered one, and that a naive
`id > last_seen` cursor would.

Steps:
 1. open a DB transaction and INSERT a `transaction` row (gets id A), don't commit
 2. make a real edit through the OL API (infobase commits id B > A)
 3. read: B is visible, A is not; a naive cursor now sits at B
 4. commit A; read again: DatabaseLog returns A's key, the naive cursor never will
 5. rollback case: hold id C, roll back, check the offset moves past C once settled
"""

import asyncio
import json
import os
import sys
import time

import psycopg
import requests

sys.path.insert(0, "/openlibrary")
os.chdir("/openlibrary")

import web

from openlibrary.config import load_config
from openlibrary.core import async_db
from scripts.solr_updater.solr_updater import DatabaseLog, parse_log

HELD_KEY = "/works/OL54120W"  # an existing work; the held tx re-references its current revision
OL = "http://web:8080"


def keys_of(records):
    return list(parse_log(records, False))


def real_edit():
    s = requests.Session()
    s.post(f"{OL}/account/login.json", json={"username": "openlibrary", "password": "openlibrary"}).raise_for_status()
    doc = s.get(f"{OL}/api/get", params={"key": "/works/OL53924W"}).json()["result"]
    for f in ("revision", "latest_revision", "created", "last_modified"):
        doc.pop(f, None)
    doc["title"] = f"Out of order {time.time_ns()}"
    r = s.post(
        f"{OL}/api/save_many",
        data=json.dumps([doc]),
        headers={"Content-Type": "application/json", "Opt": '"http://openlibrary.org/dev/docs/api"; ns=42', "42-comment": "ooo test"},
    )
    r.raise_for_status()
    return doc["key"]


async def main():
    load_config("conf/openlibrary.yml")
    p = web.config.db_parameters
    conninfo = {"dbname": p["db"], "user": p.get("user"), "host": p.get("host"), "password": p.get("pw") or ""}
    result = {}

    tx_max = await async_db.fetch_val("SELECT max(id) FROM transaction")
    store_max = await async_db.fetch_val("SELECT max(id) FROM store")
    log = DatabaseLog(settle_seconds=5)
    log.seek(f"db:{tx_max}:{store_max}")
    naive_last = tx_max

    rev = await async_db.fetch_val("SELECT latest_revision FROM thing WHERE key = %(k)s", {"k": HELD_KEY})
    held = psycopg.connect(**conninfo)
    held_id = held.execute(
        "INSERT INTO transaction (action, comment, changes, data) VALUES ('update', 'held open by out_of_order.py', %s, '{}') RETURNING id",
        (json.dumps([{"key": HELD_KEY, "revision": rev}]),),
    ).fetchone()[0]

    edited = real_edit()
    first = await log.read_records()
    first_ids = [int(r["data"]["changeset"]["id"]) for r in first]
    naive_rows = await async_db.fetch_all("SELECT id FROM transaction WHERE id > %(l)s ORDER BY id", {"l": naive_last})
    naive_last = max([r["id"] for r in naive_rows], default=naive_last)
    result["held_id"] = held_id
    result["first_read_tx_ids"] = first_ids
    result["first_read_keys"] = keys_of(first)
    result["naive_cursor_after_first_read"] = naive_last

    held.commit()
    held.close()
    second = await log.read_records()
    naive_second = await async_db.fetch_all("SELECT id FROM transaction WHERE id > %(l)s ORDER BY id", {"l": naive_last})
    result["second_read_tx_ids"] = [int(r["data"]["changeset"]["id"]) for r in second]
    result["second_read_keys"] = keys_of(second)
    result["naive_second_read_tx_ids"] = [r["id"] for r in naive_second]

    # rollback: a sequence id that never commits must not block the offset forever
    rb = psycopg.connect(**conninfo)
    rb_id = rb.execute("INSERT INTO transaction (action, changes) VALUES ('update', '[]') RETURNING id").fetchone()[0]
    real_edit()
    await log.read_records()
    rb.rollback()
    rb.close()
    offset_before_settle = log.tell()
    await asyncio.sleep(6)
    await log.read_records()
    result["rolled_back_id"] = rb_id
    result["offset_before_settle"] = offset_before_settle
    result["offset_after_settle"] = log.tell()

    await async_db.execute("DELETE FROM transaction WHERE id = %(id)s", {"id": held_id})
    await async_db.close_pool()

    checks = {
        "held tx invisible while open": held_id not in first_ids and bool(first_ids),
        "real edit read first": edited in result["first_read_keys"],
        "held tx read after commit": held_id in result["second_read_tx_ids"] and HELD_KEY in result["second_read_keys"],
        "naive cursor loses held tx": held_id not in result["naive_second_read_tx_ids"],
        "offset waits during settle": int(offset_before_settle.split(":")[1]) < rb_id,
        "offset passes rolled-back id after settle": int(result["offset_after_settle"].split(":")[1]) > rb_id,
    }
    result["checks"] = checks
    print(json.dumps(result, indent=1))
    (__import__("pathlib").Path("/shadow/out/out_of_order.json")).write_text(json.dumps(result, indent=1))
    assert all(checks.values()), checks


asyncio.run(main())

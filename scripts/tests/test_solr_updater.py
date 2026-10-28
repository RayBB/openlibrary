import sys
from unittest.mock import MagicMock

# TODO: Can we remove _init_path someday :(
sys.modules["_init_path"] = MagicMock()

import datetime as dt
import json

import pytest

from scripts.solr_updater import solr_updater
from scripts.solr_updater.solr_updater import DatabaseLog, SettlingIdCursor, parse_log


class TestParseLog:
    def test_action_save(self):
        sample_record = {
            "action": "save",
            "site": "openlibrary.org",
            "timestamp": "2022-04-07T23:03:33.942746",
            "data": {
                "comment": "Fixed author redirect",
                "key": "/books/OL34239002M",
                "query": {
                    "type": "/type/edition",
                    "authors": ["/authors/OL9388A"],
                    "languages": ["/languages/eng"],
                    "title": "Romeo and Juliet Annotated",
                    "works": ["/works/OL362427W"],
                    "key": "/books/OL34239002M",
                },
                "result": {"key": "/books/OL34239002M", "revision": 3},
                "changeset": {
                    "kind": "update",
                    "author": {"key": "/people/bluebar"},
                    "ip": "123.123.123.123",
                    "comment": "Fixed author redirect",
                    "timestamp": "2022-04-07T23:03:33.942746",
                    "bot": True,
                    "changes": [{"key": "/books/OL34239002M", "revision": 3}],
                    "data": {},
                    "id": "123456",
                    "docs": [
                        {
                            "type": {"key": "/type/edition"},
                            "authors": [{"key": "/authors/OL9388A"}],
                            "languages": [{"key": "/languages/eng"}],
                            "title": "Romeo and Juliet Annotated",
                            "works": [{"key": "/works/OL362427W"}],
                            "key": "/books/OL34239002M",
                            "latest_revision": 3,
                            "revision": 3,
                            "created": {
                                "type": "/type/datetime",
                                "value": "2021-10-01T12:56:46.152576",
                            },
                            "last_modified": {
                                "type": "/type/datetime",
                                "value": "2022-04-07T23:03:33.942746",
                            },
                        }
                    ],
                    "old_docs": [
                        {
                            "type": {"key": "/type/edition"},
                            "authors": [{"key": "/authors/OL9352911A"}],
                            "languages": [{"key": "/languages/eng"}],
                            "title": "Romeo and Juliet Annotated",
                            "works": [{"key": "/works/OL362427W"}],
                            "key": "/books/OL34239002M",
                            "latest_revision": 2,
                            "revision": 2,
                            "created": {
                                "type": "/type/datetime",
                                "value": "2021-10-01T12:56:46.152576",
                            },
                            "last_modified": {
                                "type": "/type/datetime",
                                "value": "2022-02-17T20:17:11.381695",
                            },
                        }
                    ],
                },
                "ip": "123.123.123.123",
                "author": "/people/bluebar",
            },
        }

        assert list(parse_log([sample_record], False)) == [
            "/books/OL34239002M",
            "/authors/OL9352911A",
        ]

    def test_move_edition(self):
        sample_record = {
            "action": "save_many",
            "site": "openlibrary.org",
            "timestamp": "2022-04-07T23:03:33.942746",
            "data": {
                "comment": "FMove edition",
                "key": "/books/OL34239002M",
                "query": {
                    "type": "/type/edition",
                    "authors": ["/authors/OL9388A"],
                    "languages": ["/languages/eng"],
                    "title": "Romeo and Juliet Annotated",
                    "works": ["/works/OL362427W"],
                    "key": "/books/OL34239002M",
                },
                "result": {"key": "/books/OL34239002M", "revision": 3},
                "changeset": {
                    "kind": "update",
                    "author": {"key": "/people/bluebar"},
                    "ip": "123.123.123.123",
                    "comment": "FMove edition",
                    "timestamp": "2022-04-07T23:03:33.942746",
                    "bot": False,
                    "changes": [{"key": "/books/OL34239002M", "revision": 3}],
                    "data": {},
                    "id": "123456",
                    "docs": [
                        {
                            "type": {"key": "/type/edition"},
                            "authors": [{"key": "/authors/OL9388A"}],
                            "languages": [{"key": "/languages/eng"}],
                            "title": "Romeo and Juliet Annotated",
                            "works": [{"key": "/works/OL42W"}],
                            "key": "/books/OL34239002M",
                            "latest_revision": 3,
                            "revision": 3,
                            "created": {
                                "type": "/type/datetime",
                                "value": "2021-10-01T12:56:46.152576",
                            },
                            "last_modified": {
                                "type": "/type/datetime",
                                "value": "2022-04-07T23:03:33.942746",
                            },
                        }
                    ],
                    "old_docs": [
                        {
                            "type": {"key": "/type/edition"},
                            "authors": [{"key": "/authors/OL9388A"}],
                            "languages": [{"key": "/languages/eng"}],
                            "title": "Romeo and Juliet Annotated",
                            "works": [{"key": "/works/OL362427W"}],
                            "key": "/books/OL34239002M",
                            "latest_revision": 2,
                            "revision": 2,
                            "created": {
                                "type": "/type/datetime",
                                "value": "2021-10-01T12:56:46.152576",
                            },
                            "last_modified": {
                                "type": "/type/datetime",
                                "value": "2022-02-17T20:17:11.381695",
                            },
                        }
                    ],
                },
                "ip": "123.123.123.123",
                "author": "/people/bluebar",
            },
        }

        assert list(parse_log([sample_record], False)) == [
            "/books/OL34239002M",
            "/works/OL362427W",
        ]

    def test_new_account(self):
        sample_record = {
            "action": "save_many",
            "site": "openlibrary.org",
            "timestamp": "2022-03-29T00:00:07.835173",
            "data": {
                "comment": "Created new account.",
                "query": [
                    {
                        "key": "/people/foobar",
                        "type": {"key": "/type/user"},
                        "displayname": "foobar",
                        "permission": {"key": "/people/foobar/permission"},
                    },
                    {
                        "key": "/people/foobar/usergroup",
                        "type": {"key": "/type/usergroup"},
                        "members": [{"key": "/people/foobar"}],
                    },
                    {
                        "key": "/people/foobar/permission",
                        "type": {"key": "/type/permission"},
                        "readers": [{"key": "/usergroup/everyone"}],
                        "writers": [{"key": "/people/foobar/usergroup"}],
                        "admins": [{"key": "/people/foobar/usergroup"}],
                    },
                ],
                "result": [
                    {"key": "/people/foobar", "revision": 1},
                    {"key": "/people/foobar/usergroup", "revision": 1},
                    {"key": "/people/foobar/permission", "revision": 1},
                ],
                "changeset": {
                    "kind": "new-account",
                    "author": {"key": "/people/foobar"},
                    "ip": "123.123.123.123",
                    "comment": "Created new account.",
                    "timestamp": "2022-03-29T00:00:07.835173",
                    "bot": False,
                    "changes": [
                        {"key": "/people/foobar", "revision": 1},
                        {"key": "/people/foobar/usergroup", "revision": 1},
                        {"key": "/people/foobar/permission", "revision": 1},
                    ],
                    "data": {},
                    "id": "123456",
                    "docs": [
                        {
                            "key": "/people/foobar",
                            "type": {"key": "/type/user"},
                            "displayname": "foobar",
                            "permission": {"key": "/people/foobar/permission"},
                            "latest_revision": 1,
                            "revision": 1,
                            "created": {
                                "type": "/type/datetime",
                                "value": "2022-03-29T00:00:07.835173",
                            },
                            "last_modified": {
                                "type": "/type/datetime",
                                "value": "2022-03-29T00:00:07.835173",
                            },
                        },
                        {
                            "key": "/people/foobar/usergroup",
                            "type": {"key": "/type/usergroup"},
                            "members": [{"key": "/people/foobar"}],
                            "latest_revision": 1,
                            "revision": 1,
                            "created": {
                                "type": "/type/datetime",
                                "value": "2022-03-29T00:00:07.835173",
                            },
                            "last_modified": {
                                "type": "/type/datetime",
                                "value": "2022-03-29T00:00:07.835173",
                            },
                        },
                        {
                            "key": "/people/foobar/permission",
                            "type": {"key": "/type/permission"},
                            "readers": [{"key": "/usergroup/everyone"}],
                            "writers": [{"key": "/people/foobar/usergroup"}],
                            "admins": [{"key": "/people/foobar/usergroup"}],
                            "latest_revision": 1,
                            "revision": 1,
                            "created": {
                                "type": "/type/datetime",
                                "value": "2022-03-29T00:00:07.835173",
                            },
                            "last_modified": {
                                "type": "/type/datetime",
                                "value": "2022-03-29T00:00:07.835173",
                            },
                        },
                    ],
                    "old_docs": [None, None, None],
                },
                "ip": "123.123.123.123",
                "author": "/people/foobar",
            },
        }

        assert list(parse_log([sample_record], False)) == [
            "/people/foobar",
            "/people/foobar/usergroup",
            "/people/foobar/permission",
        ]


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestSettlingIdCursor:
    def test_low_waits_for_settle(self):
        clock = FakeClock()
        cursor = SettlingIdCursor(10, settle_seconds=60, clock=clock)
        cursor.mark_read(11)
        cursor.mark_read(13)
        cursor.advance()
        assert cursor.low == 10
        assert set(cursor.seen) == {11, 13}

        clock.now += 61
        cursor.advance()
        # 12 never showed up within the settle window: given up on (rolled back)
        assert cursor.low == 13
        assert cursor.seen == {}

    def test_late_id_is_kept_but_does_not_hold_back_low(self):
        clock = FakeClock()
        cursor = SettlingIdCursor(10, settle_seconds=60, clock=clock)
        cursor.mark_read(11)
        cursor.mark_read(13)
        clock.now += 30
        cursor.mark_read(12)
        clock.now += 31
        cursor.advance()
        assert cursor.low == 13
        assert cursor.seen == {}

    def test_only_settled_ids_move_low(self):
        clock = FakeClock()
        cursor = SettlingIdCursor(0, settle_seconds=60, clock=clock)
        cursor.mark_read(1)
        cursor.mark_read(2)
        clock.now += 61
        cursor.mark_read(5)
        cursor.advance()
        assert cursor.low == 2
        assert set(cursor.seen) == {5}

    def test_old_rows_settle_immediately(self):
        clock = FakeClock()
        cursor = SettlingIdCursor(0, settle_seconds=60, clock=clock)
        cursor.mark_read(1, age=3600)
        cursor.mark_read(2, age=3600)
        cursor.mark_read(3, age=5)
        cursor.advance()
        assert cursor.low == 2
        assert set(cursor.seen) == {3}


def _doc(key, revision, **fields):
    return {"key": key, "revision": revision, "latest_revision": revision, **fields}


class FakeDB:
    """Stands in for the transaction/thing/data/store tables DatabaseLog reads."""

    def __init__(self):
        self.transactions: dict[int, dict] = {}
        self.data: dict[tuple[str, int], dict] = {}
        self.store: dict[int, dict] = {}

    def save(self, tx_id, docs, action="update", author="/people/foo"):
        for doc in docs:
            self.data[doc["key"], doc["revision"]] = doc
        self.transactions[tx_id] = {
            "id": tx_id,
            "action": action,
            "created": dt.datetime(2026, 10, 28, 12, 0, tx_id % 60),
            "comment": "edit",
            "ip": "127.0.0.1",
            "bot": False,
            "changes": json.dumps([{"key": d["key"], "revision": d["revision"]} for d in docs]),
            "data": "{}",
            "author": author,
            "age": 0,
        }

    def put(self, store_id, key, doc):
        self.store = {i: row for i, row in self.store.items() if row["key"] != key}
        self.store[store_id] = {"id": store_id, "key": key, "json": json.dumps(doc)}

    async def fetch_all(self, query, params):
        if "FROM transaction" in query:
            rows = [r for i, r in sorted(self.transactions.items()) if i > params["low"] and i not in params["seen"]]
            return rows[: params["limit"]]
        if "FROM unnest" in query:
            wanted = zip(params["keys"], params["revisions"], strict=True)
            return [{"key": k, "revision": r, "data": json.dumps(self.data[k, r])} for k, r in wanted if (k, r) in self.data]
        if "FROM store" in query:
            rows = [
                r
                for i, r in sorted(self.store.items())
                if i > params["low"]
                and i not in params["seen"]
                and (
                    r["key"] == "solr-force-update" or r["key"].startswith("solr-force-update/") or (params["load_ia_scans"] and r["key"].startswith("ia-scan/"))
                )
            ]
            return rows[: params["limit"]]
        raise AssertionError(query)

    async def fetch_val(self, query, params=None):
        assert query == "SELECT max(id) FROM store", query
        return max(self.store, default=None)


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(solr_updater.async_db, "fetch_all", db.fetch_all)
    monkeypatch.setattr(solr_updater.async_db, "fetch_val", db.fetch_val)
    return db


async def read_keys(log: DatabaseLog, load_ia_scans=False) -> list[str]:
    return list(parse_log(await log.read_records(), load_ia_scans))


class TestDatabaseLog:
    @pytest.mark.asyncio
    async def test_rebuilds_changeset_like_infobase_log(self, fake_db):
        fake_db.data["/books/OL1M", 1] = _doc("/books/OL1M", 1, works=[{"key": "/works/OL1W"}])
        fake_db.save(
            5,
            [
                _doc("/books/OL1M", 2, works=[{"key": "/works/OL2W"}]),
                _doc("/works/OL3W", 1, title="New"),
            ],
        )
        log = DatabaseLog()
        log.seek("db:4:0")

        [record] = await log.read_records()

        assert record["action"] == "save_many"
        changeset = record["data"]["changeset"]
        assert changeset["id"] == "5"
        assert changeset["author"] == {"key": "/people/foo"}
        assert [d["revision"] for d in changeset["docs"]] == [2, 1]
        assert changeset["old_docs"] == [fake_db.data["/books/OL1M", 1], None]
        # the edition moved off /works/OL1W, so the old work is reindexed too
        assert sorted(parse_log([record], False)) == ["/books/OL1M", "/works/OL1W", "/works/OL3W"]

    @pytest.mark.asyncio
    async def test_out_of_order_commit_is_not_skipped(self, fake_db, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(solr_updater.time, "monotonic", clock)
        log = DatabaseLog(settle_seconds=60)
        log.seek("db:0:0")

        fake_db.save(1, [_doc("/works/OL1W", 1)])
        fake_db.save(3, [_doc("/works/OL3W", 1)])
        assert await read_keys(log) == ["/works/OL1W", "/works/OL3W"]
        # nothing new: already-read transactions are not read again
        assert await read_keys(log) == []

        # transaction 2 got its id before 3 but committed after it
        clock.now += 30
        fake_db.save(2, [_doc("/works/OL2W", 1)])
        assert await read_keys(log) == ["/works/OL2W"]
        assert log.tell() == "db:0:0"

        clock.now += 31
        await log.read_records()
        assert log.tell() == "db:3:0"

    @pytest.mark.asyncio
    async def test_resumes_from_offset_without_skipping(self, fake_db, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(solr_updater.time, "monotonic", clock)
        for i in range(1, 4):
            fake_db.save(i, [_doc(f"/works/OL{i}W", 1)])
        log = DatabaseLog(settle_seconds=60)
        log.seek("db:0:0")
        await log.read_records()
        offset = log.tell()

        # restart before the reads settled: everything after the offset is read again
        restarted = DatabaseLog(settle_seconds=60)
        restarted.seek(offset)
        assert await read_keys(restarted) == ["/works/OL1W", "/works/OL2W", "/works/OL3W"]

    @pytest.mark.asyncio
    async def test_pages_through_large_backlogs(self, fake_db):
        for i in range(1, 251):
            fake_db.save(i, [_doc(f"/works/OL{i}W", 1)])
        log = DatabaseLog()
        log.seek("db:0:0")
        keys = await read_keys(log)
        assert len(keys) == 250
        assert await read_keys(log) == []

    @pytest.mark.asyncio
    async def test_old_backlog_does_not_pile_up(self, fake_db):
        for i in range(1, 251):
            fake_db.save(i, [_doc(f"/works/OL{i}W", 1)])
            fake_db.transactions[i]["age"] = 86400
        log = DatabaseLog(settle_seconds=600)
        log.seek("db:0:0")
        assert len(await read_keys(log)) == 250
        assert log.tell() == "db:250:0"
        assert log.transactions.seen == {}

    @pytest.mark.asyncio
    async def test_store_force_update(self, fake_db):
        log = DatabaseLog()
        log.seek("db:0:7")
        fake_db.put(7, "solr-force-update", {"type": "solr-force-update", "keys": ["/works/OL1W"]})
        assert await read_keys(log) == []

        # infobase gives the row a new id on every put
        fake_db.put(8, "solr-force-update", {"type": "solr-force-update", "keys": ["/works/OL2W", "/authors/OL1A"]})
        fake_db.put(9, "account/foo", {"type": "account"})
        assert await read_keys(log) == ["/works/OL2W", "/authors/OL1A"]

    @pytest.mark.asyncio
    async def test_store_force_update_per_request_keys(self, fake_db):
        log = DatabaseLog()
        log.seek("db:0:0")
        # two admin requests between polls: both are kept, unlike the single shared key
        fake_db.put(1, "solr-force-update/a1", {"type": "solr-force-update", "keys": ["/works/OL1W"]})
        fake_db.put(2, "solr-force-update/b2", {"type": "solr-force-update", "keys": ["/works/OL2W"]})
        assert await read_keys(log) == ["/works/OL1W", "/works/OL2W"]

    @pytest.mark.asyncio
    async def test_store_ia_scans_only_when_enabled(self, fake_db):
        fake_db.put(1, "ia-scan/foo00bar", {"type": "ia-scan", "identifier": "foo00bar"})
        log = DatabaseLog()
        log.seek("db:0:0")
        assert await read_keys(log) == []

        log = DatabaseLog(load_ia_scans=True)
        log.seek("db:0:0")
        assert await read_keys(log, load_ia_scans=True) == ["/books/ia:foo00bar"]

    @pytest.mark.asyncio
    async def test_exclude(self, fake_db):
        fake_db.save(1, [_doc("/works/OL1W", 1)], author="/people/ImportBot")
        fake_db.save(2, [_doc("/works/OL2W", 1)])
        log = DatabaseLog(exclude="/people/ImportBot")
        log.seek("db:0:0")
        assert await read_keys(log) == ["/works/OL2W"]

    @pytest.mark.asyncio
    async def test_store_cursor_moves_past_unrelated_writes(self, fake_db, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(solr_updater.time, "monotonic", clock)
        log = DatabaseLog(settle_seconds=60)
        log.seek("db:0:0")
        for i in range(1, 6):
            fake_db.put(i, f"loan-{i}", {"type": "loan"})
        assert await read_keys(log) == []
        clock.now += 61
        await log.read_records()
        assert log.tell() == "db:0:5"

    def test_offsets(self):
        assert DatabaseLog.is_db_offset("db:12:34\n")
        assert not DatabaseLog.is_db_offset("2026-10-28:0")
        log = DatabaseLog()
        log.seek("db:12:34\n")
        assert log.tell() == "db:12:34"

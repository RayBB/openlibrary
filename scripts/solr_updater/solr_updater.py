"""New script to handle solr updates.

Author: Anand Chitipothu

Changes:
2013-02-25: First version
2018-02-11: Use newer config method
"""

import asyncio
import datetime as dt
import itertools
import json
import logging
import re
import socket
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Literal, TypedDict, cast

import aiofiles
import requests

import infogami
from infogami import config
from openlibrary.config import load_config
from openlibrary.core import async_db
from openlibrary.solr import update
from openlibrary.utils.open_syllabus_project import set_osp_dump_location
from openlibrary.utils.sentry import init_sentry
from openlibrary.utils.shutdown import setup_graceful_shutdown

logger = logging.getLogger("openlibrary.solr-updater")
# FIXME: Some kind of hack introduced to work around DB connectivity issue
args: dict = {}
setup_graceful_shutdown()


def read_state_file(path, initial_state: str | None = None):
    try:
        return open(path).read()
    except OSError:
        logger.error("State file %s is not found. Reading log from the beginning of today", path)
        return initial_state or f"{dt.date.today().isoformat()}:0"


def get_default_offset():
    return dt.date.today().isoformat() + ":0"


class InfobaseLogRecord(TypedDict):
    action: str
    timestamp: str
    data: dict
    site: str


class InfobaseLogResponse(TypedDict):
    data: list[InfobaseLogRecord]
    offset: str
    """E.g. '2023-02-11:0'"""


class InfobaseLog:
    def __init__(self, hostname: str, exclude: str | None = None):
        """
        :param str hostname:
        :param str|None exclude: if specified, excludes records that include the string
        """
        self.base_url = "http://%s/openlibrary.org/log" % hostname
        self.offset = get_default_offset()
        self.exclude = exclude

    def tell(self):
        return self.offset

    def seek(self, offset):
        self.offset = offset.strip()

    def read_records(self, max_fetches=10):
        """Reads all the available log records from the server."""
        for i in range(max_fetches):
            url = f"{self.base_url}/{self.offset}?limit=100"
            logger.debug("Reading log from %s", url)
            try:
                resp = requests.get(url)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                logger.error("Failed to open URL %s", url, exc_info=True)
                if e.args and e.args[0].args == (111, "Connection refused"):
                    logger.error(
                        "make sure infogami server is working, connection refused from %s",
                        url,
                    )
                    sys.exit(1)
                raise

            try:
                d: InfobaseLogResponse = resp.json()
            except:
                logger.error("Bad JSON: %s", resp.text)
                raise
            data = d["data"]
            # no more data is available
            if not data:
                logger.debug("no more records found")
                # There's an infobase bug where we'll sometimes get 0 items, but the
                # binary offset will have incremented...?
                if "offset" in d:
                    # There's _another_ infobase bug where if you query a future date,
                    # it'll return back 2020-12-01. To avoid solrupdater getting stuck
                    # in a loop, only update the offset if it's newer than the current
                    old_day, old_boffset = self.offset.split(":")
                    old_boffset = int(old_boffset)
                    new_day, new_boffset = d["offset"].split(":")
                    new_boffset = int(new_boffset)
                    if new_day >= old_day and new_boffset >= old_boffset:
                        self.offset = d["offset"]
                return

            for record in data:
                if self.exclude and self.exclude in json.dumps(record):
                    continue
                yield record

            self.offset = d["offset"]


class SettlingIdCursor:
    """Tracks which rows of a table with a sequence-backed id have been read.

    Sequence ids are handed out before commit, so a lower id can become visible
    after a higher one. Instead of a single "last id" we keep every id read above
    `low`, and only move `low` past an id once it has been read for
    `settle_seconds`. Any id still missing below it by then is taken to be a
    rolled-back transaction.

    Rows that were already old when read (e.g. while catching up on a backlog)
    count as read when they were written, so they settle straight away instead
    of piling up in `seen`.
    """

    def __init__(self, low: int, settle_seconds: float, clock: Callable[[], float] | None = None):
        self.low = low
        """Every id <= low has been read (or given up on)."""
        self.settle_seconds = settle_seconds
        self.clock = clock or time.monotonic
        self.seen: dict[int, float] = {}
        """Ids > low that have been read, mapped to when they were first read."""

    def mark_read(self, id_: int, age: float = 0):
        """:param age: Seconds since the row was written, if known."""
        self.seen.setdefault(id_, self.clock() - max(age, 0))

    def advance(self):
        cutoff = self.clock() - self.settle_seconds
        if settled := [id_ for id_, read_at in self.seen.items() if read_at <= cutoff]:
            self.low = max(self.low, *settled)
            self.seen = {id_: read_at for id_, read_at in self.seen.items() if id_ > self.low}


class DatabaseLog:
    """Reads changes straight from the infobase database tables, instead of
    from the log files the infobase service writes.

    Yields records shaped like `InfobaseLogRecord`, so `parse_log` treats both
    sources the same way:
    - a "save_many" record per row of `transaction`, with the docs saved in it
      and their previous revisions (rebuilt from `data`)
    - a "store.put" record per row of `store` the updater cares about; infobase
      gives a store row a new id on every put, so its id works as a change cursor.
      Store deletes leave nothing behind, so "store.delete" is never produced.

    The offset looks like `db:<transaction id>:<store id>`.
    """

    PAGE_SIZE = 100

    def __init__(
        self,
        exclude: str | None = None,
        load_ia_scans: bool = False,
        settle_seconds: float = 600,
        site: str = "openlibrary.org",
    ):
        self.exclude = exclude
        self.load_ia_scans = load_ia_scans
        self.settle_seconds = settle_seconds
        self.site = site
        self.transactions = SettlingIdCursor(0, settle_seconds)
        self.store = SettlingIdCursor(0, settle_seconds)

    def tell(self) -> str:
        return f"db:{self.transactions.low}:{self.store.low}"

    def seek(self, offset: str):
        _, tx_low, store_low = offset.strip().split(":")
        self.transactions = SettlingIdCursor(int(tx_low), self.settle_seconds)
        self.store = SettlingIdCursor(int(store_low), self.settle_seconds)

    @staticmethod
    def is_db_offset(offset: str) -> bool:
        return offset.strip().startswith("db:")

    @staticmethod
    async def offset_for_date(date: str) -> str:
        """Offset of the first transaction on or after `date` (YYYY-MM-DD).

        The store has no timestamps, so store changes are read from now on.
        """
        tx_low = await async_db.fetch_val(
            "SELECT coalesce((SELECT min(id) FROM transaction WHERE created >= %(date)s) - 1, (SELECT max(id) FROM transaction), 0)",
            {"date": date},
        )
        store_low = await async_db.fetch_val("SELECT coalesce(max(id), 0) FROM store")
        return f"db:{tx_low}:{store_low}"

    async def read_records(self, max_fetches=10) -> list[InfobaseLogRecord]:
        """Reads the available records from the database, oldest first."""
        records: list[InfobaseLogRecord] = []
        for _ in range(max_fetches):
            page = await self._read_transactions()
            records += page
            if len(page) < self.PAGE_SIZE:
                break
        for _ in range(max_fetches):
            page = await self._read_store()
            records += page
            if len(page) < self.PAGE_SIZE:
                break
        self.transactions.advance()
        self.store.advance()

        if self.exclude:
            exclude = self.exclude
            records = [rec for rec in records if exclude not in json.dumps(rec)]
        return records

    async def _read_transactions(self) -> list[InfobaseLogRecord]:
        rows = await async_db.fetch_all(
            """
            SELECT t.id, t.action, t.created, t.comment, host(t.ip) AS ip, t.bot, t.changes, t.data, author.key AS author,
                extract(epoch FROM (now() AT TIME ZONE 'utc') - t.created)::float AS age
            FROM transaction t
            LEFT JOIN thing author ON author.id = t.author_id
            WHERE t.id > %(low)s AND t.id <> ALL(%(seen)s::int[])
            ORDER BY t.id
            LIMIT %(limit)s
            """,
            {"low": self.transactions.low, "seen": list(self.transactions.seen), "limit": self.PAGE_SIZE},
        )
        for row in rows:
            # `created` is the time of the save request, from before its id was handed out
            self.transactions.mark_read(row["id"], age=row["age"] or 0)

        changes_by_tx = {row["id"]: json.loads(row["changes"] or "[]") for row in rows}
        docs = await self._fetch_docs({(c["key"], c["revision"]) for changes in changes_by_tx.values() for c in changes})

        records: list[InfobaseLogRecord] = []
        for row in rows:
            changes = changes_by_tx[row["id"]]
            timestamp = row["created"].isoformat()
            changeset = {
                "id": str(row["id"]),
                "kind": row["action"],
                "author": row["author"] and {"key": row["author"]},
                "ip": row["ip"],
                "comment": row["comment"],
                "timestamp": timestamp,
                "bot": row["bot"],
                "changes": changes,
                "data": json.loads(row["data"] or "{}"),
                # A key-only doc if the revision is somehow missing, so the key still gets reindexed
                "docs": [docs.get((c["key"], c["revision"])) or {"key": c["key"]} for c in changes],
                "old_docs": [docs.get((c["key"], c["revision"] - 1)) for c in changes],
            }
            records.append(
                {
                    "action": "save_many",
                    "site": self.site,
                    "timestamp": timestamp,
                    "data": {
                        "comment": row["comment"],
                        "author": row["author"],
                        "ip": row["ip"],
                        "changeset": changeset,
                    },
                }
            )
        return records

    async def _fetch_docs(self, revisions: set[tuple[str, int]]) -> dict[tuple[str, int], dict]:
        """Fetches the given (key, revision) docs and the revision before each."""
        wanted = revisions | {(key, revision - 1) for key, revision in revisions if revision > 1}
        if not wanted:
            return {}
        keys, revs = zip(*wanted)
        rows = await async_db.fetch_all(
            """
            SELECT thing.key, data.revision, data.data
            FROM unnest(%(keys)s::text[], %(revisions)s::int[]) AS wanted(key, revision)
            JOIN thing ON thing.key = wanted.key
            JOIN data ON data.thing_id = thing.id AND data.revision = wanted.revision
            """,
            {"keys": list(keys), "revisions": list(revs)},
        )
        return {(row["key"], row["revision"]): json.loads(row["data"]) for row in rows}

    async def _read_store(self) -> list[InfobaseLogRecord]:
        # Most store writes (loans, accounts, ...) aren't ones we care about. Marking the
        # newest id as read lets the cursor move past them; read before the rows, so a
        # matching row committed in between is still picked up.
        max_id = await async_db.fetch_val("SELECT max(id) FROM store")
        rows = await async_db.fetch_all(
            """
            SELECT id, key, json
            FROM store
            WHERE id > %(low)s AND id <> ALL(%(seen)s::int[])
              AND (key = 'solr-force-update' OR key LIKE 'solr-force-update/%%' OR (%(load_ia_scans)s AND key LIKE 'ia-scan/%%'))
            ORDER BY id
            LIMIT %(limit)s
            """,
            {
                "low": self.store.low,
                "seen": list(self.store.seen),
                "load_ia_scans": self.load_ia_scans,
                "limit": self.PAGE_SIZE,
            },
        )
        for row in rows:
            self.store.mark_read(row["id"])
        if max_id is not None and len(rows) < self.PAGE_SIZE:
            self.store.mark_read(max_id)
        return [
            {
                "action": "store.put",
                "site": self.site,
                "timestamp": "",
                "data": {"key": row["key"], "data": json.loads(row["json"]) | {"_key": row["key"], "_rev": str(row["id"])}},
            }
            for row in rows
        ]


def find_keys(d: dict | list) -> Iterator[str]:
    """
    Find any keys in the given dict or list.

    >>> list(find_keys({'key': 'foo'}))
    ['foo']
    >>> list(find_keys([{}, {'key': 'bar'}]))
    ['bar']
    >>> list(find_keys([{'key': 'blue'}, {'key': 'bar'}]))
    ['blue', 'bar']
    >>> list(find_keys({'title': 'foo'}))
    []
    >>> list(find_keys({ 'works': [ {'key': 'foo'} ] }))
    ['foo']
    >>> list(find_keys({ 'authors': [ { 'author': {'key': 'foo'} } ] }))
    ['foo']
    """
    if isinstance(d, dict):
        if "key" in d:
            yield d["key"]
        for val in d.values():
            yield from find_keys(val)
    elif isinstance(d, list):
        for val in d:
            yield from find_keys(val)
    else:
        # All other types are not recursed
        return


def parse_log(records: Iterable[InfobaseLogRecord], load_ia_scans: bool):
    for rec in records:
        action = rec.get("action")

        if action in ("save", "save_many"):
            changeset = rec["data"].get("changeset", {})
            old_docs = changeset.get("old_docs", [])
            new_docs = changeset.get("docs", [])
            for before, after in zip(old_docs, new_docs):
                yield cast(str, after["key"])
                # before is None if the item is new
                if before:
                    before_keys = set(find_keys(before))
                    after_keys = set(find_keys(after))
                    # If a key was changed or was removed, the previous keys
                    # also need to be updated
                    yield from before_keys - after_keys

        elif action == "store.put":
            # Record shape: {"action": "store.put", "data": {"key": <key>, "data": <store doc>}, ...}
            data = rec.get("data", {}).get("data", {})
            key = data.get("_key", "")
            if load_ia_scans and data.get("type") == "ia-scan" and key.startswith("ia-scan/"):
                identifier = cast(str | None, data.get("identifier"))
                if identifier and is_allowed_itemid(identifier):
                    yield "/books/ia:" + identifier

            # Hack to force updating something from admin interface
            # The admin interface writes the keys to update to a document named
            # 'solr-force-update/<id>' (formerly just 'solr-force-update') in the
            # store and whatever keys are written to that are picked by this script
            elif key == "solr-force-update" or key.startswith("solr-force-update/"):
                keys = cast(list[str], data.get("keys", []))
                yield from keys

        elif action == "store.delete":
            data = rec.get("data", {})
            key = cast(str, data.get("key"))
            # An ia-scan key is deleted when that book is deleted/darked from IA.
            # Delete it from OL solr by updating that key
            if key.startswith("ia-scan/"):
                ol_key = "/works/ia:" + key.split("/")[-1]
                yield ol_key


def is_allowed_itemid(identifier: str):
    if not re.match("^[a-zA-Z0-9_.-]*$", identifier):
        return False

    # items starts with these prefixes are not books. Ignore them.
    ignore_prefixes = cast(list[str], config.get("ia_ignore_prefixes", []))
    return all(not identifier.startswith(prefix) for prefix in ignore_prefixes)


async def update_keys(keys: Iterable[str]):
    # FIXME: Some kind of hack introduced to work around DB connectivity issue
    logger.debug("Args: %s" % str(args))
    update.load_configs(args["ol_url"], args["ol_config"], "default")

    keys = [k for k in keys if update.can_update_key(k)]
    if not keys:
        return 0

    count = 0
    for chunk in itertools.batched(keys, 100, strict=False):
        count += len(chunk)
        await update.update_keys(list(chunk), commit=False)

        # Caches should not persist between different calls to update_keys!
        update.data_provider.clear_cache()

    if count:
        logger.info("updated %d documents", count)

    return count


async def main(
    ol_config: str,
    osp_dump: Path | None = None,
    debugger: bool = False,
    state_file: str = "solr-update.state",
    exclude_edits_containing: str | None = None,
    ol_url="http://openlibrary.org/",  # Has side effects, is read globally
    socket_timeout: int = 10,
    load_ia_scans: bool = False,
    initial_state: str | None = None,
    source: Literal["infobase", "db"] = "infobase",
    settle_seconds: int = 600,
):
    """
    Useful environment variables:
    - OL_SOLR_BASE_URL: Override the Solr base URL
    - OL_SOLR_NEXT: Set to true if running with next version of Solr/schema

    :param debugger: Wait for a debugger to attach before beginning
    :param exclude_edits_containing: Don't index matching edits
    :param solr_url: If wanting to override what's in the config file
    :param initial_state: State to use if state file doesn't exist. Defaults to today.
    :param source: Where to read changes from: the infobase service's log, or the database directly.
        An infobase offset (e.g. `2026-10-28:0`) in the state file is converted to a database offset
        on start, so switching to `db` picks up from the same day.
    :param settle_seconds: With `--source db`, how long to keep looking for transactions that commit
        out of order (see SettlingIdCursor).
    """
    FORMAT = "%(asctime)-15s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=FORMAT)
    logger.info("BEGIN solr_updater")

    if debugger:
        import debugpy  # noqa: T100

        logger.info("Enabling debugger attachment (attach if it hangs here)")
        debugpy.listen(address=("0.0.0.0", 3000))  # noqa: T100
        logger.info("Waiting for debugger to attach...")
        debugpy.wait_for_client()  # noqa: T100
        logger.info("Debugger attached to port 3000")

    # Sometimes archive.org requests blocks forever.
    # Setting a timeout will make the request fail instead of waiting forever.
    socket.setdefaulttimeout(socket_timeout)

    set_osp_dump_location(osp_dump)

    logger.info("loading config from %s", ol_config)
    load_config(ol_config)
    init_sentry(getattr(infogami.config, "sentry", {}))

    offset = read_state_file(state_file, initial_state)

    logfile: InfobaseLog | DatabaseLog
    if source == "db":
        logfile = DatabaseLog(exclude=exclude_edits_containing, load_ia_scans=load_ia_scans, settle_seconds=settle_seconds)
        if not DatabaseLog.is_db_offset(offset):
            infobase_offset = offset
            offset = await DatabaseLog.offset_for_date(infobase_offset.split(":")[0])
            logger.info("converted infobase offset %s to database offset %s", infobase_offset, offset)
    else:
        if DatabaseLog.is_db_offset(offset):
            raise ValueError(f"State file {state_file} has a database offset ({offset}); run with --source db")
        logfile = InfobaseLog(config.get("infobase_server"), exclude=exclude_edits_containing)
    logfile.seek(offset)

    while True:
        records = await logfile.read_records() if isinstance(logfile, DatabaseLog) else logfile.read_records()
        keys = parse_log(records, load_ia_scans)
        count = await update_keys(keys)

        if logfile.tell() != offset:
            offset = logfile.tell()
            logger.info("saving offset %s", offset)
            async with aiofiles.open(state_file, "w") as f:
                await f.write(offset)

        # don't sleep after committing some records.
        # While the commit was on, some more edits might have happened.
        if count == 0:
            logger.debug("No more log records available, sleeping...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    from scripts.solr_builder.solr_builder.fn_to_cli import FnToCLI

    cli = FnToCLI(main)
    args = cli.args_dict()
    cli.run()

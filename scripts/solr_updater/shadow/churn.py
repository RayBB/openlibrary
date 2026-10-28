# Reference-only test harness; lint rules relaxed.
# ruff: noqa: BLE001, RUF012
"""Make a stream of realistic edits against a local Open Library, through the
same write paths the site uses (infobase save / save_many / new, the
lists API and the admin solr force-update page).

Usage: python churn.py [--rounds N] [--seed S] [--base http://localhost:8080]
Prints one JSON line per operation to stdout (op name + keys touched).
"""

import argparse
import json
import random
import sys
import time

import requests

META_FIELDS = ("revision", "latest_revision", "created", "last_modified", "id")


class OL:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        r = self.s.post(f"{self.base}/account/login.json", json={"username": "openlibrary", "password": "openlibrary"})
        r.raise_for_status()

    def get(self, key):
        # raw infobase doc (some .json views, like lists, are reshaped)
        r = self.s.get(f"{self.base}/api/get", params={"key": key})
        r.raise_for_status()
        doc = r.json()["result"]
        for f in META_FIELDS:
            doc.pop(f, None)
        return doc

    def query(self, **q):
        r = self.s.get(f"{self.base}/query.json", params=q)
        r.raise_for_status()
        return [d["key"] for d in r.json()]

    def _api(self, name, payload, comment="churn", action=None):
        headers = {"Content-Type": "application/json", "Opt": '"http://openlibrary.org/dev/docs/api"; ns=42', "42-comment": comment}
        if action:
            headers["42-action"] = action
        r = self.s.post(f"{self.base}/api/{name}", data=json.dumps(payload), headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"{name} failed {r.status_code}: {r.text[:300]}")
        return r.json()

    def save_many(self, docs, action=None):
        return self._api("save_many", docs, action=action)

    def new(self, doc):
        return self._api("new", doc)

    def save(self, doc):
        # PUT /key -> infobase "save" event (single doc)
        headers = {"Content-Type": "application/json", "Opt": '"http://openlibrary.org/dev/docs/api"; ns=42', "42-comment": "churn put"}
        r = self.s.put(f"{self.base}{doc['key']}", data=json.dumps(doc), headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"PUT {doc['key']} failed {r.status_code}: {r.text[:300]}")

    def new_list(self, name, seeds):
        r = self.s.post(f"{self.base}/people/openlibrary/lists.json", json={"name": name, "seeds": seeds})
        if r.status_code >= 400:
            raise RuntimeError(f"new list failed {r.status_code}: {r.text[:300]}")
        return r.json()["key"]

    def force_update(self, keys):
        r = self.s.post(f"{self.base}/admin/solr", data={"keys": " ".join(keys)})
        r.raise_for_status()


class Churn:
    def __init__(self, ol: OL, rng: random.Random):
        self.ol = ol
        self.rng = rng
        self.works = ol.query(type="/type/work", limit=1000)
        self.editions = ol.query(type="/type/edition", limit=1000)
        self.authors = ol.query(type="/type/author", limit=1000)
        self.lists = ol.query(type="/type/list", limit=1000)
        self.n = 0

    def word(self):
        return self.rng.choice(["Tide", "Lantern", "Orchard", "Cipher", "Harbor", "Meadow", "Quartz", "Ember", "Atlas", "Willow"])

    def title(self):
        self.n += 1
        return f"{self.word()} {self.word()} {self.n}"

    def live(self, pool):
        """Pick a key that still has a real (non-deleted/redirected) doc."""
        for _ in range(20):
            key = self.rng.choice(pool)
            doc = self.ol.get(key)
            if doc["type"]["key"] not in ("/type/delete", "/type/redirect"):
                return key, doc
        raise LookupError("no live docs left in pool")

    # --- operations -----------------------------------------------------------------
    def edit_work_title(self):
        key, doc = self.live(self.works)
        doc["title"] = self.title()
        self.ol.save_many([doc])
        return [key]

    def edit_work_subjects_put(self):
        key, doc = self.live(self.works)
        doc["subjects"] = sorted({*doc.get("subjects", [])[:3], f"Churn {self.word()}"})
        self.ol.save(doc)
        return [key]

    def edit_edition(self):
        key, doc = self.live(self.editions)
        doc["title"] = self.title()
        doc["publishers"] = [f"{self.word()} Press"]
        self.ol.save_many([doc])
        return [key, *[w["key"] for w in doc.get("works", [])]]

    def move_edition(self):
        # edition moves from work A to work B: A must be reindexed too (old_docs diff)
        key, doc = self.live(self.editions)
        target, _ = self.live(self.works)
        old = [w["key"] for w in doc.get("works", [])]
        doc["works"] = [{"key": target}]
        self.ol.save_many([doc])
        return [key, target, *old]

    def swap_work_author(self):
        key, doc = self.live(self.works)
        author, _ = self.live(self.authors)
        old = [a["author"]["key"] for a in doc.get("authors", []) if "author" in a]
        doc["authors"] = [{"type": {"key": "/type/author_role"}, "author": {"key": author}}]
        self.ol.save_many([doc])
        return [key, author, *old]

    def rename_author(self):
        key, doc = self.live(self.authors)
        doc["name"] = f"{self.word()} {self.word()}son"
        self.ol.save(doc)
        return [key]

    def create_book(self):
        author = self.ol.new({"type": {"key": "/type/author"}, "name": f"New {self.word()} Writer"})
        work = self.ol.new(
            {
                "type": {"key": "/type/work"},
                "title": self.title(),
                "authors": [{"type": {"key": "/type/author_role"}, "author": {"key": author}}],
                "subjects": ["Churn testing"],
            }
        )
        edition = self.ol.new(
            {
                "type": {"key": "/type/edition"},
                "title": self.title(),
                "works": [{"key": work}],
                "authors": [{"key": author}],
                "publish_date": str(self.rng.randint(1900, 2025)),
                "isbn_13": [f"978{self.rng.randint(10**9, 10**10 - 1)}"],
            }
        )
        self.authors.append(author)
        self.works.append(work)
        self.editions.append(edition)
        return [author, work, edition]

    def delete_edition(self):
        key, doc = self.live(self.editions)
        self.ol.save_many([{"key": key, "type": {"key": "/type/delete"}}], action="delete-book")
        return [key, *[w["key"] for w in doc.get("works", [])]]

    def merge_works(self):
        # what the merge UI does: duplicate becomes a redirect, its editions move to the master
        master, _ = self.live(self.works)
        dupe, _ = self.live(self.works)
        if master == dupe:
            return []
        editions = list(self.ol.query(type="/type/edition", works=dupe, limit=100))
        docs = [{"key": dupe, "type": {"key": "/type/redirect"}, "location": master}]
        for e in editions:
            d = self.ol.get(e)
            d["works"] = [{"key": master}]
            docs.append(d)
        self.ol.save_many(docs, action="merge-works")
        return [master, dupe, *editions]

    def merge_authors(self):
        master, _ = self.live(self.authors)
        dupe, _ = self.live(self.authors)
        if master == dupe:
            return []
        docs = [{"key": dupe, "type": {"key": "/type/redirect"}, "location": master}]
        works = []
        for w in self.ol.query(**{"type": "/type/work", "authors.author.key": dupe, "limit": 50}):
            d = self.ol.get(w)
            for a in d.get("authors", []):
                if a.get("author", {}).get("key") == dupe:
                    a["author"] = {"key": master}
            docs.append(d)
            works.append(w)
        self.ol.save_many(docs, action="merge-authors")
        return [master, dupe, *works]

    def create_list(self):
        seeds = [{"key": self.live(self.rng.choice([self.works, self.editions, self.authors]))[0]} for _ in range(3)]
        seeds.append("subject:churn_testing")
        key = self.ol.new_list(f"Churn list {self.title()}", seeds)
        self.lists.append(key)
        return [key, *[s["key"] if isinstance(s, dict) else s for s in seeds]]

    def edit_list(self):
        if not self.lists:
            return self.create_list()
        key, doc = self.live(self.lists)
        seeds = doc.get("seeds", [])
        removed = seeds.pop(0) if seeds else None
        added = {"key": self.live(self.works)[0]}
        seeds.append(added)
        doc["seeds"] = seeds
        doc["name"] = f"Churn list {self.title()}"
        self.ol.save_many([doc])
        return [key, added["key"], *([removed["key"]] if isinstance(removed, dict) else [])]

    def force_update(self):
        keys = [self.rng.choice(self.works), self.rng.choice(self.authors)]
        self.ol.force_update(keys)
        return keys

    OPS = {
        "edit_work_title": 6,
        "edit_work_subjects_put": 4,
        "edit_edition": 6,
        "move_edition": 3,
        "swap_work_author": 3,
        "rename_author": 3,
        "create_book": 3,
        "delete_edition": 1,
        "merge_works": 1,
        "merge_authors": 1,
        "create_list": 2,
        "edit_list": 3,
        "force_update": 1,
    }

    def run(self, rounds, pause):
        names = list(self.OPS)
        weights = list(self.OPS.values())
        for i in range(rounds):
            op = self.rng.choices(names, weights)[0]
            try:
                keys = getattr(self, op)()
                print(json.dumps({"t": time.time(), "i": i, "op": op, "keys": keys}), flush=True)
            except Exception as e:  # keep churning; report failures
                print(json.dumps({"i": i, "op": op, "error": str(e)[:300]}), flush=True)
            if pause:
                time.sleep(pause)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=100)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--pause", type=float, default=0)
    p.add_argument("--base", default="http://localhost:8080")
    p.add_argument("--only", help="run only this op (for smoke testing)")
    a = p.parse_args()
    churn = Churn(OL(a.base), random.Random(a.seed))
    if a.only:
        churn.OPS = {a.only: 1}
    churn.run(a.rounds, a.pause)
    sys.exit(0)

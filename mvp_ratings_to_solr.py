#!/usr/bin/env python3
"""
mvp_ratings_to_solr.py — Stream ratings + reading_log from solr_duckdb parquet -> Solr atomic {"set":} updates.

No Gold rebuild. Uses DuckDB GROUP BY on pre-aggregated parquet (already zstd) and streams via httpx.Client.
Reads /root/solr_duckdb/parquet/ratings.parquet (838k rows, 495k works) and reading_log.parquet (12.5M, 3.18M works).

Usage:
  .venv/bin/python mvp_ratings_to_solr.py --solr http://localhost:8985/solr/openlibrary --batch 10000
  .venv/bin/python mvp_ratings_to_solr.py --ratings /root/solr_duckdb/parquet/ratings.parquet \
    --reading-log /root/solr_duckdb/parquet/reading_log.parquet --dry-run --limit 5
"""

from __future__ import annotations

import argparse
import math
import time

import duckdb
import httpx


def compute_sortable_rating(counts: list[int]) -> float:
    n = counts
    N = sum(n, 0)
    K = len(n)
    z = 1.65
    if N + K == 0:
        return 0.0
    # mean of (k+1)*(n_k+1)/(N+K)
    mean = sum(((k + 1) * (n_k + 1) / (N + K) for k, n_k in enumerate(n)), 0.0)
    # second moment
    m2 = sum((((k + 1) ** 2) * (n_k + 1) / (N + K) for k, n_k in enumerate(n)), 0.0)
    var = (m2 - mean * mean) / (N + K + 1) if (N + K + 1) != 0 else 0.0
    return mean - z * math.sqrt(var) if var > 0 else mean


def work_ratings_summary(counts: list[int]) -> dict:
    total = sum(counts, 0)
    avg = (sum((k * c for k, c in enumerate(counts, 1)), 0) / total) if total else 0.0
    sortable = compute_sortable_rating(counts)
    return {
        "ratings_average": avg,
        "ratings_sortable": sortable,
        "ratings_count": total,
        "ratings_count_1": counts[0],
        "ratings_count_2": counts[1],
        "ratings_count_3": counts[2],
        "ratings_count_4": counts[3],
        "ratings_count_5": counts[4],
    }


def main():  # noqa: PLR0915
    ap = argparse.ArgumentParser()
    ap.add_argument("--ratings", default="/root/solr_duckdb/parquet/ratings.parquet")
    ap.add_argument("--reading-log", default="/root/solr_duckdb/parquet/reading_log.parquet")
    ap.add_argument("--solr", default="http://localhost:8985/solr/openlibrary")
    ap.add_argument("--batch", type=int, default=10000)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--skip-ratings", action="store_true")
    ap.add_argument("--skip-reading-log", action="store_true")
    args = ap.parse_args()

    solr_base = args.solr.rstrip("/")
    update_url = f"{solr_base}/update"
    limit_sql = f" LIMIT {args.limit}" if args.limit else ""

    print(f"[ratings] ratings={args.ratings} reading_log={args.reading_log} solr={solr_base} batch={args.batch}", flush=True)
    t0 = time.time()

    try:
        r = httpx.get(f"{solr_base}/admin/ping", params={"wt": "json"}, timeout=10)
        print(f"[ping] {r.status_code}", flush=True)
    except Exception as e:
        print(f"[ping failed] {e}")
        raise

    con = duckdb.connect()

    # ---------- Ratings ----------
    if not args.skip_ratings:
        print("[ratings] aggregating...", flush=True)
        q0 = time.time()
        cur = con.execute(f"""
            SELECT WorkKey,
                   count(*) FILTER (WHERE Rating=1) AS c1,
                   count(*) FILTER (WHERE Rating=2) AS c2,
                   count(*) FILTER (WHERE Rating=3) AS c3,
                   count(*) FILTER (WHERE Rating=4) AS c4,
                   count(*) FILTER (WHERE Rating=5) AS c5
            FROM '{args.ratings}'
            GROUP BY WorkKey
            {limit_sql}
        """)
        rows = cur.fetchall()
        print(f"[ratings] aggregated {len(rows)} distinct works in {time.time() - q0:.2f}s", flush=True)

        if args.dry_run:
            for work, c1, c2, c3, c4, c5 in rows[:5]:
                summ = work_ratings_summary([c1, c2, c3, c4, c5])
                print(f"{work} {summ}", flush=True)
        else:
            headers = {"Content-Type": "application/json"}
            total = 0
            batches = 0
            post_t = 0.0
            with httpx.Client(timeout=60.0) as client:
                # Build docs in memory then stream batches — rows already aggregated (495k) ~ small
                docs = []
                for work, c1, c2, c3, c4, c5 in rows:
                    summ = work_ratings_summary([c1, c2, c3, c4, c5])
                    # atomic set — include type for new docs to satisfy required field
                    doc = {"key": work, "type": {"set": "work"}}
                    for k, v in summ.items():
                        doc[k] = {"set": v}
                    docs.append(doc)

                for i in range(0, len(docs), args.batch):
                    batch = docs[i : i + args.batch]
                    payload = __import__("json").dumps(batch).encode()
                    st = time.time()
                    res = client.post(
                        f"{update_url}?commitWithin=60000",
                        content=payload,
                        headers=headers,
                        params={"commitWithin": "60000"},
                    )
                    post_t += time.time() - st
                    try:
                        res.raise_for_status()
                    except Exception:
                        print(f"[ratings batch {batches} failed] {res.status_code} {res.text[:1000]}", flush=True)
                        raise
                    total += len(batch)
                    batches += 1
                    if batches % 10 == 0:
                        print(f"[ratings batch {batches}] {len(batch)} -> total {total} avg {post_t / batches:.2f}s", flush=True)
            print(f"[ratings done] total={total} batches={batches} post_t={post_t:.2f}s", flush=True)

    # ---------- Reading Log ----------
    if not args.skip_reading_log:
        print("[reading_log] aggregating...", flush=True)
        q0 = time.time()
        cur = con.execute(f"""
            SELECT WorkKey,
                   count(*) FILTER (WHERE Shelf='Want to Read') AS want,
                   count(*) FILTER (WHERE Shelf='Currently Reading') AS curr,
                   count(*) FILTER (WHERE Shelf='Already Read') AS already,
                   count(*) FILTER (WHERE Shelf='Stopped Reading') AS stopped
            FROM '{args.reading_log}'
            GROUP BY WorkKey
            {limit_sql}
        """)
        rows = cur.fetchall()
        print(f"[reading_log] aggregated {len(rows)} distinct works in {time.time() - q0:.2f}s", flush=True)

        if args.dry_run:
            for work, want, curr, already, stopped in rows[:5]:
                total = want + curr + already + stopped
                print(f"{work} want={want} curr={curr} already={already} stopped={stopped} total={total}", flush=True)
        else:
            headers = {"Content-Type": "application/json"}
            total = 0
            batches = 0
            post_t = 0.0
            with httpx.Client(timeout=60.0) as client:
                docs = []
                for work, want, curr, already, stopped in rows:
                    total_c = want + curr + already + stopped
                    doc = {
                        "key": work,
                        "type": {"set": "work"},
                        "readinglog_count": {"set": total_c},
                        "want_to_read_count": {"set": want},
                        "currently_reading_count": {"set": curr},
                        "already_read_count": {"set": already},
                        "stopped_reading_count": {"set": stopped},
                    }
                    docs.append(doc)

                for i in range(0, len(docs), args.batch):
                    batch = docs[i : i + args.batch]
                    payload = __import__("json").dumps(batch).encode()
                    st = time.time()
                    res = client.post(
                        f"{update_url}?commitWithin=60000",
                        content=payload,
                        headers=headers,
                        params={"commitWithin": "60000"},
                    )
                    post_t += time.time() - st
                    try:
                        res.raise_for_status()
                    except Exception:
                        print(f"[reading_log batch {batches} failed] {res.status_code} {res.text[:1000]}", flush=True)
                        raise
                    total += len(batch)
                    batches += 1
                    if batches % 20 == 0:
                        print(f"[reading_log batch {batches}] {len(batch)} -> total {total} avg {post_t / batches:.2f}s", flush=True)
            print(f"[reading_log done] total={total} batches={batches} post_t={post_t:.2f}s", flush=True)

    elapsed = time.time() - t0
    print(f"[all done] elapsed={elapsed:.2f}s", flush=True)

    if not args.dry_run and not args.no_commit:
        print("[commit] POST update?commit=true", flush=True)
        with httpx.Client(timeout=120.0) as client:
            res = client.get(update_url, params={"commit": "true"})
            print(f"[commit] {res.status_code}", flush=True)
            for field in ["ratings_average", "readinglog_count"]:
                res = client.get(f"{solr_base}/select", params={"q": f"{field}:*", "rows": "0", "wt": "json"})
                try:
                    cnt = res.json()["response"]["numFound"]
                    print(f"[verify] {field}:* numFound={cnt}", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"[verify {field} failed] {e}", flush=True)


if __name__ == "__main__":
    main()

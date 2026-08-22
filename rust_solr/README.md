# rust_solr — DuckDB (A) → Rust Transform → Gold Parquet (minimal)

Keeps DuckDB for queries (silver `work_key` 21× win `mvp_silver_py.py:1`), Rust does `WorkSolrBuilder` build + Parquet write. Minimal schema `key, doc_json, title, edition_count` per user decision, logical diff.

## Quick run (test with small samples first)

```bash
cargo build --release  # ~5s incremental, 1.5m cold (libduckdb-sys bundled)
./target/release/rust_solr --limit 10 --out /tmp/rust_10.parquet \
  --bronze-works /root/openlibrary/lake/bronze/works.parquet \
  --silver-editions /root/openlibrary/lake/silver/editions.parquet \
  --bronze-authors /root/openlibrary/lake/bronze/authors.parquet

# 100/1000/10000
./target/release/rust_solr --limit 100 --out lake/gold/rust_100.parquet --bronze-works lake/bronze/works.parquet --silver-editions lake/silver/editions.parquet --bronze-authors lake/bronze/authors.parquet
./target/release/rust_solr --limit 1000 --out lake/gold/rust_1000.parquet --bronze-works lake/bronze/works.parquet --silver-editions lake/silver/editions.parquet --bronze-authors lake/bronze/authors.parquet
./target/release/rust_solr --limit 10000 --out lake/gold/rust_10k.parquet --bronze-works lake/bronze/works.parquet --silver-editions lake/silver/editions.parquet --bronze-authors lake/bronze/authors.parquet
```

Works must be run from repo root OR pass absolute paths (binary runs with `Connection::open_in_memory`, so paths are passed to DuckDB SQL).

## Structure

```
src/
  main.rs         # clap CLI, rayon chunk filter (mvp_gold_DC.py:104-115)
  query.rs        # DuckDB: sample_keys temp table + JOIN silver work_key + authors via appender
  transform/mod.rs # WorkSolrBuilder port (minimal core fields)
  helpers/
    sort_title.rs # edition.py:105
    ddc.rs        # utils/ddc.py:49 + choose_sorting_ddc
    lcc.rs        # utils/lcc.py:115
    isbn.rs       # opposite_isbn
    mod.rs        # uniq, subject_name_to_key, datetimestr_to_int
  parquet.rs      # arrow 54 ArrowWriter zstd(3) minimal schema
```

## Bench (10k, START_AT=/works/OL1W)

| Variant | total | prep | build | docs/s |
|---------|-------|------|-------|--------|
| Python `mvp_gold_DC.py:15` idle | 19.17s | 10.58s | 8.52s | 1173 |
| Python loaded | 43.98s | 24.41s | 19.44s | 514 |
| **Rust A (this)** | **12.62s** | **10.21s** | **2.19s** | **4559** |

`12.62s` vs `19.17s` → 1.5× total, 3.9× build. Full 14.4M est `build 0.88h total 5.05h` vs Python `3.41h/7.67h`.

Smaller samples (stable prep ~2.5-10s, build scales linearly):
- 10: prep 4.45s build 0.01s 1029 docs/s
- 100: prep 7.61s build 0.03s 3848 docs/s
- 1000: prep 9.30s build 0.25s 3968 docs/s

## Logical diff (minimal core fields)

Checked `key, type, title, title_sort, edition_count, edition_key, author_key/name/facet, publisher, publish_year, first_publish_year, language, isbn, lcc, lcc_sort, ddc, ddc_sort, seed, subject*, place*, person*, time*, last_modified_i, cover_i`:

- **10:** 0/10 mismatches (pass)
- **100:** 5/100 mismatches (all `lcc_sort` tie-breaking where same short_len but different year, e.g. `NA-1353.00000000.B67 A4 2013` vs `2014` — both valid max among set)
- **1000:** 59/1000 mismatches (same tie cause, ~5.9% lcc_sort/ddc_sort)

Set iteration order for `choose_sorting_lcc/ddc` is nondeterministic in both Python (`set`) and Rust (`HashSet`) when multiple candidates tie on `short_len`. Logical diff should allow either tie-winner.

Full `doc_json` still omits `editions` nested, `by_statement`, `number_of_pages_median`, `cover_edition_key`, `ia`, `ebook_*`, `chapter`, `format` — deferred for next iteration (needs EditionSolrBuilder full port). Core fields now parity-checked.

## Next steps

1. Port remaining `EditionSolrBuilder` fields (editions, by_statement, median pages, cover_edition_key, format, chapter, ebook_access with `ocaid -> unclassified` logic) to reach 0% core mismatch and close `editions` gap.
2. Fix LCC tie determinism (sort before max) if strict byte parity needed.
3. Add `cargo test` fixtures from Python `test_ddc.py`/`test_lcc.py` + golden 10-work JSON.
4. Solr loading deferred (`mvp_load.py:24` `commitWithin=60000`).

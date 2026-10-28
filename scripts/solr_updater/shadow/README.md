# Solr updater shadow-test harness

Reference only. This compares the infobase-log solr-updater with `--source db` (RAY-76) on a local
docker stack. It is not production tooling.

- `run.sh <rounds> <writers> <seed>` does a full experiment: fresh Solr cores `shadow_infobase`,
  `shadow_db` and `shadow_truth`, both updaters following live while `churn.py` makes edits, then a
  from-scratch reindex and a comparison. Set `RESTART_DB_AFTER=<seconds>` to kill and restart the
  db updater mid-run. Results go to `out/` (`report.json`, `*-keys.jsonl`).
- `churn.py` makes realistic edits through the site's write APIs (works, editions, authors, lists,
  merges, deletes, admin force-updates).
- `latency.py out/` reports how long each updater took to pick up each edit.
- `out_of_order.py` proves on real Postgres that a transaction committing after a higher-numbered
  one is not skipped.
- `replay_day.py` reads today's history through both readers (no indexing) and compares the keys.

Three extra Solr cores need more than the local 128m heap; start Solr with `solr-heap.yaml` added
as an extra compose file. Afterwards, unload the shadow cores. If Solr crashes, it may rewrite
`managed-schema.xml` in the openlibrary core, and the local start script then refuses to boot;
copying `conf/solr/conf/managed-schema.xml` back into the core fixes it.

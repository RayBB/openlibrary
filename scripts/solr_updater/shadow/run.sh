#!/bin/bash
# Full shadow experiment: setup -> follow both sources live while churning -> truth -> compare
# usage: run.sh <rounds-per-worker> <workers> <seed>
set -euo pipefail
S=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$S" rev-parse --show-toplevel)
ROUNDS=${1:-50}; WORKERS=${2:-4}; SEED=${3:-1}
cd "$REPO"
run() { docker compose run --rm -T --no-deps -v "$S":/shadow solr-updater python /shadow/shadow.py "$@" 2>&1 | grep -v "HOSTNAME\|^0.0 (\|DEBUG\|Open Syllabus\|preload_" ; }

docker compose exec -T solr sh -c 'for c in shadow_infobase shadow_db shadow_truth; do mkdir -p /var/solr/data/$c; [ -d /var/solr/data/$c/conf ] || cp -r /opt/solr/server/solr/configsets/olconfig /var/solr/data/$c/conf; done' 2>/dev/null
run setup > "$S/out/setup.log"
rm -f "$S/out/stop"
run follow --source infobase > "$S/out/follow-infobase.log" &
IB=$!
run follow --source db > "$S/out/follow-db.log" &
DB=$!
sleep 15  # let both followers start polling

: > "$S/out/churn.jsonl"
pids=()
for w in $(seq 1 "$WORKERS"); do
  uv run -q --with requests python "$S/churn.py" --rounds "$ROUNDS" --seed $((SEED * 100 + w)) >> "$S/out/churn.jsonl" &
  pids+=($!)
done
if [ -n "${RESTART_DB_AFTER:-}" ]; then
  # simulate a crash of the db updater mid-churn, then restart it from its state file
  sleep "$RESTART_DB_AFTER"
  docker ps -q --filter "name=solr-updater-run" | while read c; do
    if docker exec "$c" pgrep -f "source db" >/dev/null 2>&1; then docker kill "$c" >/dev/null && echo "killed db follower $c"; fi
  done
  wait $DB || true
  echo "db state at restart: $(cat "$S/out/db.state")"
  run follow --source db > "$S/out/follow-db-2.log" &
  DB=$!
fi
wait "${pids[@]}"
echo "churn done: $(grep -c '"keys"' "$S/out/churn.jsonl") ok, $(grep -c '"error"' "$S/out/churn.jsonl") errors"
touch "$S/out/stop"
wait $IB $DB
run truth > "$S/out/truth.log"
run compare | tee "$S/out/summary.json"

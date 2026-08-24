#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  contracts/events/job-failed.schema.json
  contracts/examples/job-failed.json
  deploy/postgres/migrations/0004_worker_results.sql
  algorithms/rainpulse_algo/worker/contracts.py
  algorithms/rainpulse_algo/worker/domain_contracts.py
  algorithms/rainpulse_algo/worker/handlers.py
  algorithms/rainpulse_algo/worker/object_store.py
  algorithms/rainpulse_algo/worker/runtime.py
  algorithms/rainpulse_algo/worker/simulation.py
  algorithms/rainpulse_algo/worker/__main__.py
  algorithms/worker.Dockerfile
  scripts/worker_smoke_test.sh
)

for path in "${required_files[@]}"; do
  test -f "$path" || { printf 'missing RP-005 file: %s\n' "$path" >&2; exit 1; }
done

rg --quiet 'rainpulse.jobs.failed' contracts/events/README.md
rg --quiet '^  simulation-worker:' deploy/docker-compose.yaml
rg --quiet 'RAINPULSE_WORKER_HEALTH_ADDR' deploy/docker-compose.yaml
rg --quiet 'inbox_events_job_terminal_result_uidx' deploy/postgres/migrations/0004_worker_results.sql
rg --quiet 'force_failure' algorithms/rainpulse_algo/worker/simulation.py
rg --quiet 'CopySource' algorithms/rainpulse_algo/worker/object_store.py
rg --quiet 'message\.ack' algorithms/rainpulse_algo/worker/runtime.py
rg --quiet 'class TaskHandler' algorithms/rainpulse_algo/worker/runtime.py
rg --quiet 'radar-decode-synthetic' algorithms/rainpulse_algo/worker/handlers.py
rg --quiet 'mosaic-qpe-synthetic' algorithms/rainpulse_algo/worker/handlers.py
rg --quiet 'nowcast-input-synthetic' algorithms/rainpulse_algo/worker/handlers.py
rg --quiet 'RAINPULSE_WORKER_PROFILE' deploy/docker-compose.yaml

printf 'RP-005 Worker SDK structure checks passed\n'

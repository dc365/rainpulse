#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

compose_file=${RAINPULSE_COMPOSE_FILE:-deploy/docker-compose.yaml}
env_file=${RAINPULSE_COMPOSE_ENV_FILE:-deploy/.env}
api_url=${RAINPULSE_API_URL:-http://127.0.0.1:8080}
timeout_seconds=${RAINPULSE_SMOKE_TIMEOUT:-90}

if [[ ! -f "$env_file" ]]; then
  printf 'Compose environment file not found: %s\n' "$env_file" >&2
  exit 1
fi

compose=(docker compose --progress quiet --env-file "$env_file" -f "$compose_file")

wait_for_state() {
  local url=$1
  local field=$2
  local expected=$3
  local deadline=$((SECONDS + timeout_seconds))

  while true; do
    response=$(curl --fail --silent --show-error "$url" 2>/dev/null || true)
    if [[ -n "$response" ]] && python3 -c '
import json
import sys

field, expected = sys.argv[1:]
value = json.load(sys.stdin)
if isinstance(value, list):
    value = value[0] if value else {}
raise SystemExit(0 if value.get(field) == expected else 1)
' "$field" "$expected" <<<"$response"; then
      return 0
    fi
    if ((SECONDS >= deadline)); then
      printf 'timed out waiting for %s=%s at %s\n' "$field" "$expected" "$url" >&2
      return 1
    fi
    sleep 1
  done
}

"${compose[@]}" exec -T simulation-worker python -c \
  "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8091/healthz'))['status'] == 'ready'"

success=$("${compose[@]}" run --rm --no-deps orchestrator simulate | tail -n 1)
read -r success_run success_job < <(python3 -c '
import json
import sys
value = json.load(sys.stdin)
print(value["run_id"], value["job_id"])
' <<<"$success")
wait_for_state "$api_url/api/v1/runs/$success_run" status BASELINE_READY
wait_for_state "$api_url/api/v1/runs/$success_run/jobs" status SUCCEEDED

"${compose[@]}" run --rm --no-deps minio-init stat \
  "rainpulse/${RAINPULSE_MINIO_BUCKET:-rainpulse}/simulations/$success_run/forecast.zarr/_SUCCESS.json" >/dev/null

# Replay with a new request event UUID. The Worker must reuse the committed
# marker and the control plane must still have exactly one terminal inbox row.
"${compose[@]}" run --rm --no-deps orchestrator replay "$success_job" >/dev/null
sleep 2

failure=$("${compose[@]}" run --rm --no-deps orchestrator simulate-failure | tail -n 1)
read -r failure_run failure_job < <(python3 -c '
import json
import sys
value = json.load(sys.stdin)
print(value["run_id"], value["job_id"])
' <<<"$failure")
wait_for_state "$api_url/api/v1/runs/$failure_run" status FAILED
wait_for_state "$api_url/api/v1/runs/$failure_run/jobs" status FAILED

failure_code=$(curl --fail --silent --show-error \
  "$api_url/api/v1/runs/$failure_run/jobs" | \
  python3 -c 'import json,sys; print(json.load(sys.stdin)[0].get("error_code", ""))')
if [[ "$failure_code" != "SIMULATED_FAILURE" ]]; then
  printf 'unexpected Worker failure code: %s\n' "$failure_code" >&2
  exit 1
fi

success_inbox=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM inbox_events WHERE job_id = '$success_job' AND event_type = 'job.completed'")
failure_inbox=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM inbox_events WHERE job_id = '$failure_job' AND event_type = 'job.failed'")
if [[ "$success_inbox" -ne 1 || "$failure_inbox" -ne 1 ]]; then
  printf 'unexpected Worker inbox ledgers: success=%s failure=%s\n' "$success_inbox" "$failure_inbox" >&2
  exit 1
fi

temporary_objects=$("${compose[@]}" run --rm --no-deps minio-init find \
  "rainpulse/${RAINPULSE_MINIO_BUCKET:-rainpulse}/_temporary" 2>/dev/null || true)
if [[ -n "$temporary_objects" ]]; then
  printf 'temporary Worker objects were not cleaned up\n' >&2
  exit 1
fi

printf 'RP-004 Worker smoke test passed: health, success, atomic marker, replay idempotency, failure\n'

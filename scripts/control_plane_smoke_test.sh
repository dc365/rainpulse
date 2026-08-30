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

admin_token=${RAINPULSE_ADMIN_TOKEN:-}
if [[ -z "$admin_token" ]]; then
  admin_token=$("${compose[@]}" config --format json | python3 -c '
import json
import sys

config = json.load(sys.stdin)
print(config.get("services", {}).get("api", {}).get("environment", {}).get("RAINPULSE_ADMIN_TOKEN", ""))
')
fi
if [[ -z "$admin_token" ]]; then
  printf 'RAINPULSE_ADMIN_TOKEN is required for the control-plane smoke test\n' >&2
  exit 1
fi
if [[ "$admin_token" == *$'\r'* || "$admin_token" == *$'\n'* ]]; then
  printf 'RAINPULSE_ADMIN_TOKEN contains an invalid line break\n' >&2
  exit 1
fi

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

wait_for_either_state() {
  local url=$1
  local field=$2
  local first=$3
  local second=$4
  local deadline=$((SECONDS + timeout_seconds))

  while true; do
    response=$(curl --fail --silent --show-error "$url" 2>/dev/null || true)
    if [[ -n "$response" ]] && python3 -c '
import json
import sys

field, first, second = sys.argv[1:]
value = json.load(sys.stdin)
if isinstance(value, list):
    value = value[0] if value else {}
raise SystemExit(0 if value.get(field) in {first, second} else 1)
' "$field" "$first" "$second" <<<"$response"; then
      return 0
    fi
    if ((SECONDS >= deadline)); then
      printf 'timed out waiting for %s in {%s,%s} at %s\n' "$field" "$first" "$second" "$url" >&2
      return 1
    fi
    sleep 1
  done
}

simulation=$("${compose[@]}" run --rm --no-deps orchestrator simulate | tail -n 1)
read -r run_id job_id < <(python3 -c '
import json
import sys

value = json.load(sys.stdin)
print(value["run_id"], value["job_id"])
' <<<"$simulation")

wait_for_either_state "$api_url/api/v1/runs/$run_id" status BASELINE_RUNNING BASELINE_READY
wait_for_either_state "$api_url/api/v1/runs/$run_id/jobs" status RUNNING SUCCEEDED

"${compose[@]}" run --rm --no-deps orchestrator complete "$job_id" >/dev/null
wait_for_state "$api_url/api/v1/runs/$run_id" status BASELINE_READY
wait_for_state "$api_url/api/v1/runs/$run_id/jobs" status SUCCEEDED

# A second logical completion has a new event UUID but the same job UUID. It
# must be ACKed without applying the state transition a second time.
"${compose[@]}" run --rm --no-deps orchestrator complete "$job_id" >/dev/null

rerun=$(printf 'Authorization: Bearer %s\n' "$admin_token" | \
  curl --fail --silent --show-error --header @- --request POST \
    "$api_url/api/v1/admin/runs/$run_id/rerun")
rerun_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<<"$rerun")
if [[ "$rerun_id" == "$run_id" ]]; then
  printf 'rerun reused the source run UUID\n' >&2
  exit 1
fi

rerun_jobs_url="$api_url/api/v1/runs/$rerun_id/jobs"
wait_for_either_state "$rerun_jobs_url" status RUNNING SUCCEEDED
rerun_job_id=$(curl --fail --silent --show-error "$rerun_jobs_url" | \
  python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["job_id"])')
"${compose[@]}" run --rm --no-deps orchestrator complete "$rerun_job_id" >/dev/null
wait_for_state "$api_url/api/v1/runs/$rerun_id" status BASELINE_READY

sse=$(curl --silent --no-buffer --max-time 2 \
  "$api_url/api/v1/events/stream?run_id=$rerun_id" 2>/dev/null || true)
if [[ "$sse" != *"event: run.updated"* || "$sse" != *'"status":"BASELINE_READY"'* ]]; then
  printf 'SSE stream did not contain the completed rerun state\n' >&2
  exit 1
fi

published=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM outbox_events WHERE aggregate_id IN ('$job_id', '$rerun_job_id') AND status = 'published'")
processed=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM inbox_events WHERE job_id IN ('$job_id', '$rerun_job_id')")
if [[ "$published" -ne 2 || "$processed" -ne 2 ]]; then
  printf 'unexpected message ledgers: outbox=%s inbox=%s\n' "$published" "$processed" >&2
  exit 1
fi

workflow_simulation=$("${compose[@]}" run --rm --no-deps orchestrator simulate-workflows | tail -n 1)
read -r analysis_id scan_a_id scan_b_id < <(python3 -c '
import json
import sys

value = json.load(sys.stdin)
print(value["analysis_id"], *value["scan_ids"])
' <<<"$workflow_simulation")

scan_a=$(curl --fail --silent --show-error "$api_url/api/v1/radar-scans/$scan_a_id")
scan_b=$(curl --fail --silent --show-error "$api_url/api/v1/radar-scans/$scan_b_id")
analysis=$(curl --fail --silent --show-error "$api_url/api/v1/analysis-cycles/$analysis_id")
python3 -c '
import json
import sys

scan_a, scan_b, analysis = map(json.loads, sys.argv[1:])
assert scan_a["status"] == "RADAR_GRID_READY", scan_a
assert scan_b["status"] == "FAILED", scan_b
assert analysis["status"] == "ANALYSIS_READY", analysis
assert analysis["radar_count"] == 1, analysis
assert analysis["degraded_reason"], analysis
states = {item["radar_id"]: item["state"] for item in analysis["radars"]}
assert states == {"synthetic_radar_a": "PARTICIPATING", "synthetic_radar_b": "FAILED"}, states
' "$scan_a" "$scan_b" "$analysis"

radar_b_status=$(curl --fail --silent --show-error \
  "$api_url/api/v1/radars/synthetic_radar_b/status")
python3 -c '
import json
import sys

status = json.load(sys.stdin)
assert status["health"] == "UNAVAILABLE", status
assert status["participating_in_latest_analysis"] is False, status
' <<<"$radar_b_status"

analysis_sse=$(curl --silent --no-buffer --max-time 2 \
  "$api_url/api/v1/events/stream?analysis_id=$analysis_id" 2>/dev/null || true)
if [[ "$analysis_sse" != *"event: analysis.cycle.updated"* || \
      "$analysis_sse" != *'"status":"ANALYSIS_READY"'* ]]; then
  printf 'analysis SSE did not contain the degraded-ready cycle\n' >&2
  exit 1
fi

printf 'RP-004 control-plane smoke passed: forecast plus radar/analysis workflows, partial-radar degradation, API and SSE\n'

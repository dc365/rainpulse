#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

compose_file=${RAINPULSE_COMPOSE_FILE:-deploy/docker-compose.yaml}
env_file=${RAINPULSE_COMPOSE_ENV_FILE:-deploy/.env}
api_url=${RAINPULSE_API_URL:-http://127.0.0.1:8080}
timeout_seconds=${RAINPULSE_SMOKE_TIMEOUT:-300}
sample=${RAINPULSE_RP006_SAMPLE:-/data/Weather/RADA/FMT_L2_Z959X_SBD/2026/08/24/Z_RADR_I_Z9598_20260824175915_O_DOR_SAD_CAP_FMT.bin.bz2}
config=/opt/rainpulse/configs/radars/z9598.yaml
volume_start=2026-06-15T11:59:15.520660Z
volume_end=2026-06-15T12:04:42.090681Z

[[ -f "$env_file" ]] || {
  printf 'Compose environment file not found: %s\n' "$env_file" >&2
  exit 1
}
[[ -f "$sample" ]] || {
  printf 'RP-007 golden sample not found: %s\n' "$sample" >&2
  exit 1
}

compose=(docker compose --progress quiet --env-file "$env_file" -f "$compose_file")
workflow=$(
  "${compose[@]}" run --rm --no-deps orchestrator radar-decode \
    "$config" "$sample" "$volume_start" "$volume_end" | tail -n 1
)
read -r scan_id run_id job_id < <(python3 -c '
import json
import sys

value = json.load(sys.stdin)
print(value["scan_id"], value["run_id"], value["job_id"])
' <<<"$workflow")

deadline=$((SECONDS + timeout_seconds))
while true; do
  scan=$(curl --fail --silent --show-error "$api_url/api/v1/radar-scans/$scan_id" 2>/dev/null || true)
  if [[ -n "$scan" ]] && python3 -c '
import json
import sys

raise SystemExit(0 if json.load(sys.stdin).get("status") in {"NORMALIZED", "QC_RUNNING", "QC_READY"} else 1)
' <<<"$scan"; then
    break
  fi
  if ((SECONDS >= deadline)); then
    printf 'timed out waiting for radar scan %s to normalize\n' "$scan_id" >&2
    "${compose[@]}" logs --tail 80 orchestrator radar-decode-worker >&2 || true
    exit 1
  fi
  sleep 2
done

status=$(curl --fail --silent --show-error "$api_url/api/v1/radars/z9598/status")
statuses=$(curl --fail --silent --show-error "$api_url/api/v1/radars/status")
python3 -c '
import json
import sys

status = json.loads(sys.argv[1])
statuses = json.loads(sys.argv[2])
assert status["radar_id"] == "z9598", status
assert status["lifecycle"] == "draft", status
assert status["config_version"] == "z9598-fmt-v1", status
assert status["health"] == "DEGRADED", status
assert status["scan_status"] in {"NORMALIZED", "QC_RUNNING", "QC_READY"}, status
health = status["health_metrics"]
assert health["health_profile_version"] == "rp007-integrity-v1", health
assert health["expected_sweep_count"] == health["actual_sweep_count"] == 11, health
assert health["actual_radial_count"] == 3994, health
assert health["missing_sweep_numbers"] == [], health
assert health["scan_completeness"] >= 0.99, health
assert health["channel_status"] == "OK", health
assert -120 <= health["noise_level"]["horizontal_dbm"] <= -70, health
assert -120 <= health["noise_level"]["vertical_dbm"] <= -70, health
assert health["noise_level"]["sample_count"] > 0, health
assert {"CONFIG_NOT_READY", "SOURCE_TIME_MISMATCH"}.issubset(health["health_reasons"]), health
assert "NOISE_OUT_OF_RANGE" not in health["health_reasons"], health
assert {item["field"] for item in health["field_availability"]} == {"DBZH", "PHIDP", "RHOHV", "SNR", "SW", "VR", "ZDR"}, health
assert any(item["radar_id"] == "z9598" and item["health"] == "DEGRADED" for item in statuses), statuses
' "$status" "$statuses"

marker="rainpulse/${RAINPULSE_MINIO_BUCKET:-rainpulse}/radar/normalized/z9598/$scan_id/volume.zarr/_SUCCESS.json"
health_object="rainpulse/${RAINPULSE_MINIO_BUCKET:-rainpulse}/radar/normalized/z9598/$scan_id/volume.zarr/health/summary.json"
"${compose[@]}" run --rm --no-deps minio-init stat "$marker" >/dev/null
object_health=$("${compose[@]}" run --rm --no-deps minio-init cat "$health_object")
python3 -c '
import json
import sys

value = json.load(sys.stdin)
assert value["radar_id"] == "z9598", value
assert value["health"] == "DEGRADED", value
assert value["channel_status"] == "OK", value
' <<<"$object_health"

"${compose[@]}" run --rm --no-deps orchestrator replay "$job_id" >/dev/null
sleep 3
inbox_count=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM inbox_events WHERE job_id = '$job_id' AND event_type = 'job.completed'")
health_count=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM radar_health_metrics WHERE scan_id = '$scan_id'")
if [[ "$inbox_count" -ne 1 || "$health_count" -ne 1 ]]; then
  printf 'unexpected radar result ledgers: inbox=%s health=%s\n' "$inbox_count" "$health_count" >&2
  exit 1
fi

printf 'RP-007 radar health workflow passed: raw asset -> NATS -> real decode -> Zarr/health -> PostgreSQL/API\n'

#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

compose_file=${RAINPULSE_COMPOSE_FILE:-deploy/docker-compose.yaml}
env_file=${RAINPULSE_COMPOSE_ENV_FILE:-deploy/.env}
api_url=${RAINPULSE_API_URL:-http://127.0.0.1:8080}
timeout_seconds=${RAINPULSE_SMOKE_TIMEOUT:-360}
sample=${RAINPULSE_RP006_SAMPLE:-/data/Weather/RADA/FMT_L2_Z959X_SBD/2026/08/24/Z_RADR_I_Z9598_20260824175915_O_DOR_SAD_CAP_FMT.bin.bz2}
radar_config=/opt/rainpulse/configs/radars/z9598.yaml
qc_config=/opt/rainpulse/configs/qc/rp008-basic-v1.yaml
volume_start=2026-06-15T11:59:15.520660Z
volume_end=2026-06-15T12:04:42.090681Z

[[ -f "$env_file" ]] || {
  printf 'Compose environment file not found: %s\n' "$env_file" >&2
  exit 1
}
[[ -f "$sample" ]] || {
  printf 'RP-008 golden sample not found: %s\n' "$sample" >&2
  exit 1
}

compose=(docker compose --progress quiet --env-file "$env_file" -f "$compose_file")
decode=$(
  "${compose[@]}" run --rm --no-deps orchestrator radar-decode \
    "$radar_config" "$sample" "$volume_start" "$volume_end" | tail -n 1
)
scan_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["scan_id"])' <<<"$decode")

deadline=$((SECONDS + timeout_seconds))
while true; do
  scan=$(curl --fail --silent --show-error "$api_url/api/v1/radar-scans/$scan_id" 2>/dev/null || true)
  if [[ -n "$scan" ]] && python3 -c '
import json,sys
scan=json.load(sys.stdin)
ready = scan.get("status") in {"NORMALIZED", "QC_RUNNING", "QC_READY"} or (
    scan.get("status") == "FAILED" and bool(scan.get("normalized_uri"))
)
raise SystemExit(0 if ready else 1)
' <<<"$scan"; then
    break
  fi
  if ((SECONDS >= deadline)); then
    printf 'timed out waiting for radar scan %s to normalize\n' "$scan_id" >&2
    "${compose[@]}" logs --tail 100 orchestrator radar-decode-worker >&2 || true
    exit 1
  fi
  sleep 2
done

qc=$(
  "${compose[@]}" run --rm --no-deps orchestrator radar-qc \
    "$scan_id" "$qc_config" | tail -n 1
)
job_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$qc")

deadline=$((SECONDS + timeout_seconds))
while true; do
  scan=$(curl --fail --silent --show-error "$api_url/api/v1/radar-scans/$scan_id" 2>/dev/null || true)
  if [[ -n "$scan" ]] && python3 -c '
import json,sys
raise SystemExit(0 if json.load(sys.stdin).get("status") == "QC_READY" else 1)
' <<<"$scan"; then
    break
  fi
  if ((SECONDS >= deadline)); then
    printf 'timed out waiting for radar scan %s to finish QC\n' "$scan_id" >&2
    "${compose[@]}" logs --tail 120 orchestrator radar-qc-worker >&2 || true
    exit 1
  fi
  sleep 2
done

summary=$(curl --fail --silent --show-error "$api_url/api/v1/radar-scans/$scan_id/qc-summary")
status=$(curl --fail --silent --show-error "$api_url/api/v1/radars/z9598/status")
python3 -c '
import json,sys
summary=json.loads(sys.argv[1])
status=json.loads(sys.argv[2])
assert summary["scan_id"] == status["latest_scan_id"], (summary,status)
assert summary["radar_id"] == "z9598", summary
assert summary["qc_profile"] == "rp008-basic-v1", summary
assert summary["qc_pipeline_version"] == "rp008-basic-1.0.1", summary
assert summary["flag_definition_version"] == "qc-flags-v1", summary
assert summary["health_state"] == "DEGRADED", summary
assert 0 < summary["mean_quality_index"] <= 1, summary
assert summary["valid_gate_count"] > 0, summary
assert summary["missing_gate_count"] > 0, summary
assert summary["low_quality_gate_count"] <= summary["valid_gate_count"], summary
assert summary["no_rain_gate_count"] >= 0, summary
assert summary["module_statuses"]["radial_interference"] == "applied", summary
assert summary["module_statuses"]["static_ground_clutter"] == "skipped", summary
assert summary["module_statuses"]["sea_ap"] == "skipped", summary
assert status["scan_status"] == "QC_READY", status
assert status["qc_metrics"]["qc_profile"] == "rp008-basic-v1", status
' "$summary" "$status"

marker="rainpulse/${RAINPULSE_MINIO_BUCKET:-rainpulse}/radar/qc/z9598/$scan_id/volume.zarr/_SUCCESS.json"
summary_object="rainpulse/${RAINPULSE_MINIO_BUCKET:-rainpulse}/radar/qc/z9598/$scan_id/volume.zarr/qc/summary.json"
"${compose[@]}" run --rm --no-deps minio-init stat "$marker" >/dev/null
object_summary=$("${compose[@]}" run --rm --no-deps minio-init cat "$summary_object")
python3 -c '
import json,sys
value=json.load(sys.stdin)
assert value["radar_id"] == "z9598", value
assert value["qc_profile"] == "rp008-basic-v1", value
assert value["module_statuses"]["static_ground_clutter"] == "skipped", value
' <<<"$object_summary"

"${compose[@]}" run --rm --no-deps orchestrator replay "$job_id" >/dev/null
sleep 3
inbox_count=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM inbox_events WHERE job_id = '$job_id' AND event_type = 'job.completed'")
qc_count=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM radar_qc_metrics WHERE scan_id = '$scan_id'")
if [[ "$inbox_count" -ne 1 || "$qc_count" -ne 1 ]]; then
  printf 'unexpected QC result ledgers: inbox=%s qc=%s\n' "$inbox_count" "$qc_count" >&2
  exit 1
fi

printf 'RP-008 basic polar QC workflow passed: normalized Zarr -> NATS -> QC Zarr -> PostgreSQL/API\n'

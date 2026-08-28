#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

compose_file=${RAINPULSE_COMPOSE_FILE:-deploy/docker-compose.yaml}
env_file=${RAINPULSE_COMPOSE_ENV_FILE:-deploy/.env}
api_url=${RAINPULSE_API_URL:-http://127.0.0.1:8080}
timeout_seconds=${RAINPULSE_SMOKE_TIMEOUT:-900}
grid_profile=/opt/rainpulse/configs/gridding/rp016-hybrid-v1.yaml

[[ -f "$env_file" ]] || {
  printf 'Compose environment file not found: %s\n' "$env_file" >&2
  exit 1
}

compose=(docker compose --progress quiet --env-file "$env_file" -f "$compose_file")
scan_id=${RAINPULSE_RP009_SCAN_ID:-}
if [[ -z "$scan_id" ]]; then
  scans=$(curl --fail --silent --show-error "$api_url/api/v1/radar-scans")
  scan_id=$(python3 -c '
import json,sys
for item in json.load(sys.stdin)["items"]:
    if item["radar_id"] == "z9598" and item["status"] in {"QC_READY", "RADAR_GRID_READY"} and item.get("qc_uri"):
        print(item["scan_id"])
        break
' <<<"$scans")
fi
[[ -n "$scan_id" ]] || {
  printf 'no accepted Z9598 QC scan is available for RP-009\n' >&2
  exit 1
}

grid=$(
  "${compose[@]}" run --rm --no-deps orchestrator radar-grid \
    "$scan_id" "$grid_profile" | tail -n 1
)
job_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$grid")

deadline=$((SECONDS + timeout_seconds))
while true; do
  scan=$(curl --fail --silent --show-error "$api_url/api/v1/radar-scans/$scan_id" 2>/dev/null || true)
  if [[ -n "$scan" ]] && python3 -c '
import json,sys
raise SystemExit(0 if json.load(sys.stdin).get("status") == "RADAR_GRID_READY" else 1)
' <<<"$scan"; then
    break
  fi
  if ((SECONDS >= deadline)); then
    printf 'timed out waiting for radar scan %s to finish gridding\n' "$scan_id" >&2
    "${compose[@]}" logs --tail 160 orchestrator radar-grid-worker >&2 || true
    exit 1
  fi
  sleep 2
done

marker="rainpulse/rainpulse/radar/grid/z9598/$scan_id/hybrid-scan-1.1.0/grid.zarr/_SUCCESS.json"
"${compose[@]}" run --rm --no-deps minio-init stat "$marker" >/dev/null
marker_json=$("${compose[@]}" run --rm --no-deps minio-init cat "$marker")
data_prefix=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("data_prefix", ""))' <<<"$marker_json")
artifact_root=${marker%/_SUCCESS.json}
if [[ -n "$data_prefix" ]]; then
  artifact_root="$artifact_root/$data_prefix"
fi
summary_object="$artifact_root/grid/summary.json"
summary=$("${compose[@]}" run --rm --no-deps minio-init cat "$summary_object")
python3 -c '
import json,sys
summary=json.loads(sys.argv[1])
scan=json.loads(sys.argv[2])
assert summary["scan_id"] == scan["scan_id"], (summary,scan)
assert summary["radar_id"] == "z9598", summary
assert summary["grid_id"] == "fuzhou_118_123_25_27_0p01deg_v1", summary
assert summary["algorithm_version"] == "hybrid-scan-1.1.0", summary
assert summary["dem_asset_version"] == "copernicus-dem-glo30-2022-v1", summary
assert summary["vertical_datum_status"] == "unverified_engineering", summary
assert summary["operational_eligible"] is False, summary
assert "vertical_datum_unverified" in summary["operational_reasons"], summary
assert summary["grid_cell_count"] == 501 * 201, summary
assert 0 < summary["valid_cell_count"] < summary["grid_cell_count"], summary
assert 0 < summary["valid_coverage_ratio"] < 1, summary
assert 0 <= summary["mean_quality_index"] <= 1, summary
assert summary["selection_counts"], summary
assert scan["status"] == "RADAR_GRID_READY", scan
assert scan["grid_uri"].endswith("/hybrid-scan-1.1.0/grid.zarr"), scan
' "$summary" "$scan"

"${compose[@]}" run --rm --no-deps orchestrator replay "$job_id" >/dev/null
sleep 3
inbox_count=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM inbox_events WHERE job_id = '$job_id' AND event_type = 'job.completed'")
grid_count=$("${compose[@]}" exec -T postgres psql -X -U rainpulse -d rainpulse -Atc \
  "SELECT count(*) FROM radar_grid_metrics WHERE scan_id = '$scan_id'")
if [[ "$inbox_count" -ne 1 || "$grid_count" -ne 1 ]]; then
  printf 'unexpected grid result ledgers: inbox=%s grid=%s\n' "$inbox_count" "$grid_count" >&2
  exit 1
fi

printf 'RP-009 workflow passed: QCRadarVolume -> polar DEM blockage -> Hybrid Scan -> RadarGrid\n'

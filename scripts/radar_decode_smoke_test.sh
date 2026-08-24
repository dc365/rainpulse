#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

compose_file=${RAINPULSE_COMPOSE_FILE:-deploy/docker-compose.yaml}
env_file=${RAINPULSE_COMPOSE_ENV_FILE:-deploy/.env}
sample=${RAINPULSE_RP006_SAMPLE:-/data/Weather/RADA/FMT_L2_Z959X_SBD/2026/08/24/Z_RADR_I_Z9598_20260824175915_O_DOR_SAD_CAP_FMT.bin.bz2}
config=/opt/rainpulse/configs/radars/z9598.yaml
health_config=/opt/rainpulse/configs/health/rp007-integrity-v1.yaml

[[ -f "$env_file" ]] || {
  printf 'Compose environment file not found: %s\n' "$env_file" >&2
  exit 1
}
[[ -f "$sample" ]] || {
  printf 'RP-006 golden sample not found: %s\n' "$sample" >&2
  exit 1
}

compose=(docker compose --progress quiet --env-file "$env_file" -f "$compose_file")
summary=$("${compose[@]}" run --rm --no-deps --entrypoint python radar-decode-worker \
  -m rainpulse_algo.radar decode \
  --input "$sample" \
  --config "$config" \
  --health-config "$health_config" \
  --output /tmp/rp006-golden-volume.zarr \
  --asset-id 44444444-4444-4444-8444-444444444444 \
  --source-uri "file://$sample" | tail -n 1)

python3 -c '
import json
import sys

summary = json.load(sys.stdin)
assert summary["radar_id"] == "z9598", summary
assert summary["input_sha256"] == "63266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f678", summary
assert summary["sweep_count"] == 11, summary
assert summary["ray_count"] == 3994, summary
assert summary["fields"] == ["DBZH", "PHIDP", "RHOHV", "SNR", "SW", "VR", "ZDR"], summary
assert summary["volume_start_time_utc"] == "2026-06-15T11:59:15.520660+00:00", summary
assert summary["volume_end_time_utc"] == "2026-06-15T12:04:42.090681+00:00", summary
assert summary["object_count"] > 100, summary
assert summary["size_bytes"] > 1_000_000, summary
assert any("header time is authoritative" in item for item in summary["warnings"]), summary
health = summary["health"]
assert health["health"] == "DEGRADED", health
assert health["scan_completeness"] >= 0.99, health
assert health["expected_sweep_count"] == health["actual_sweep_count"] == 11, health
assert health["missing_sweep_numbers"] == [], health
assert health["channel_status"] == "UNKNOWN", health
assert health["noise_level"]["sample_count"] == 0, health
assert {"CONFIG_NOT_READY", "SOURCE_TIME_MISMATCH", "NOISE_TELEMETRY_MISSING"}.issubset(health["health_reasons"]), health
' <<<"$summary"

printf 'RP-007 radar integrity smoke passed: Z9598 RSTM 2.0 -> validated health-aware sweep-group Zarr\n'

#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  configs/health/rp007-integrity-v1.yaml
  configs/schemas/radar-health.schema.json
  algorithms/rainpulse_algo/radar/health.py
  deploy/postgres/migrations/0006_radar_health.sql
  scripts/radar_health_smoke_test.sh
)

for path in "${required_files[@]}"; do
  [[ -f "$path" ]] || {
    printf 'missing RP-007 file: %s\n' "$path" >&2
    exit 1
  }
done

rg --quiet 'health/summary.json' algorithms/rainpulse_algo/radar/zarr_volume.py
rg --quiet 'RAINPULSE_RADAR_HEALTH_CONFIG' deploy/docker-compose.yaml
rg --quiet 'CREATE TABLE radar_health_metrics' deploy/postgres/migrations/0006_radar_health.sql
rg --quiet 'radar.decode.requested.v1' services/control/internal/orchestration/events.go
rg --quiet '/radars/status' contracts/openapi.yaml

printf 'RP-007 radar integrity and health structure checks passed\n'

#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/schemas/radar-grid-profile.schema.json"
  "configs/gridding/rp009-hybrid-v1.1.yaml"
  "configs/gridding/rp016-hybrid-v1.yaml"
  "algorithms/rainpulse_algo/radar/blockage.py"
  "algorithms/rainpulse_algo/radar/dem.py"
  "algorithms/rainpulse_algo/radar/hybrid.py"
  "algorithms/rainpulse_algo/radar/grid_zarr.py"
  "algorithms/rainpulse_algo/radar/grid_worker.py"
  "algorithms/tests/test_radar_grid.py"
  "deploy/postgres/migrations/0008_radar_grid.sql"
  "scripts/radar_grid_smoke_test.sh"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-009 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

grep -q 'RAINPULSE_WORKER_PROFILE: radar-grid-hybrid' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'RAINPULSE_RADAR_GRID_CONFIG: /opt/rainpulse/configs/gridding/rp016-hybrid-v1.yaml' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'RadarGridJobType' \
  "$repo_root/services/control/internal/orchestration/events.go"

printf 'RP-009 baseline and RP-016 hardened Hybrid Scan artifacts are present.\n'

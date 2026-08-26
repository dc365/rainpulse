#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/schemas/radar-mosaic-profile.schema.json"
  "configs/mosaic/rp010-qi-mosaic-v1.yaml"
  "configs/mosaic/rp016-qi-mosaic-v1.yaml"
  "contracts/data/radar-mosaic.md"
  "contracts/events/analysis-mosaic-requested-v2.schema.json"
  "algorithms/rainpulse_algo/radar/mosaic.py"
  "algorithms/rainpulse_algo/radar/mosaic_profile.py"
  "algorithms/rainpulse_algo/radar/mosaic_zarr.py"
  "algorithms/rainpulse_algo/radar/mosaic_worker.py"
  "algorithms/tests/test_radar_mosaic.py"
  "deploy/postgres/migrations/0009_analysis_mosaic.sql"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-010 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

grep -q 'RAINPULSE_WORKER_PROFILE: analysis-mosaic-qi' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'RAINPULSE_RADAR_MOSAIC_CONFIG: /opt/rainpulse/configs/mosaic/rp016-qi-mosaic-v1.yaml' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'AnalysisMosaicJobType' \
  "$repo_root/services/control/internal/orchestration/events.go"

printf 'RP-010 baseline and RP-016 hardened mosaic artifacts are present.\n'

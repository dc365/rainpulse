#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/schemas/pysteps-lk-profile.schema.json"
  "configs/nowcast/rp014-pysteps-lk-v1.yaml"
  "contracts/data/forecast-output.md"
  "contracts/events/forecast-pysteps-lk-requested.schema.json"
  "contracts/events/forecast-baseline-ready.schema.json"
  "algorithms/rainpulse_algo/nowcast/pysteps_lk.py"
  "algorithms/rainpulse_algo/nowcast/forecast_zarr.py"
  "algorithms/rainpulse_algo/nowcast/pysteps_worker.py"
  "algorithms/tests/test_pysteps_lk.py"
  "deploy/postgres/migrations/0013_pysteps_lk.sql"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-014 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

grep -q 'RAINPULSE_WORKER_PROFILE: pysteps-lk' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'PystepsLKJobType' \
  "$repo_root/services/control/internal/orchestration/events.go"
grep -q 'RunBaselineReady' \
  "$repo_root/services/control/internal/workflow/state.go"
grep -q 'persistence_rain_rate' \
  "$repo_root/contracts/data/forecast-output.md"

printf 'RP-014 pySTEPS-LK baseline artifacts are present.\n'

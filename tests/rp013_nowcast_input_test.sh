#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/schemas/nowcast-input-profile.schema.json"
  "configs/nowcast/rp013-fixed-5min-v1.yaml"
  "configs/nowcast/rp013-fixed-5min-v1.1.yaml"
  "configs/nowcast/rp040-historical-replay-v3.yaml"
  "contracts/data/nowcast-input.md"
  "contracts/events/nowcast-input-requested.schema.json"
  "contracts/events/nowcast-input-ready.schema.json"
  "algorithms/rainpulse_algo/nowcast/input_profile.py"
  "algorithms/rainpulse_algo/nowcast/input_zarr.py"
  "algorithms/rainpulse_algo/nowcast/input_worker.py"
  "algorithms/tests/test_nowcast_input.py"
  "deploy/postgres/migrations/0012_nowcast_input.sql"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-013 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

grep -q 'RAINPULSE_WORKER_PROFILE: nowcast-input' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'RAINPULSE_NOWCAST_INPUT_CONFIG: /opt/rainpulse/configs/nowcast/rp040-historical-replay-v3.yaml' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'NowcastInputJobType' \
  "$repo_root/services/control/internal/orchestration/events.go"
grep -q 'RunInputReady' \
  "$repo_root/services/control/internal/workflow/state.go"

printf 'RP-013 fixed-step NowcastInput artifacts are present.\n'

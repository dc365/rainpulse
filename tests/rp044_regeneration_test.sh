#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  scripts/regenerate_forecasts.sh
  scripts/backfill_historical_steps.py
  algorithms/tests/test_manual_regeneration.py
)

for path in "${required_files[@]}"; do
  test -s "$path" || { printf 'missing RP-044 file: %s\n' "$path" >&2; exit 1; }
done

bash -n scripts/regenerate_forecasts.sh
rg --quiet -- '--header @-' scripts/regenerate_forecasts.sh
if rg --quiet -- '--header "Authorization:' scripts/regenerate_forecasts.sh; then
  printf 'admin token is exposed through a curl argument\n' >&2
  exit 1
fi
rg --quiet '^    RegenerationRequest:' contracts/openapi.yaml
rg --quiet 'manual-regeneration/' services/control/internal/orchestration/service.go
rg --quiet 'outsideForecastLookback' services/control/cmd/orchestrator/planner.go
rg --quiet -- '--force' scripts/backfill_historical_steps.py
rg --quiet 'prune_cycle_versions' scripts/backfill_fujian_nowcastnet_shadow.py

if REGEN_PRESET=unsupported bash scripts/regenerate_forecasts.sh >/dev/null 2>&1; then
  printf 'unsupported regeneration preset unexpectedly succeeded\n' >&2
  exit 1
fi

printf 'RP-044 bounded manual regeneration checks passed\n'

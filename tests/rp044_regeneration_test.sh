#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  deploy/postgres/migrations/0016_manual_regeneration.sql
  deploy/postgres/migrations/0017_full_pipeline_regeneration.sql
  scripts/regenerate_forecasts.sh
  scripts/run_retained_product_generator.py
  scripts/backfill_historical_steps.py
  scripts/backfill_fujian_nowcastnet_shadow_5min.py
  algorithms/rainpulse_algo/products/version_retention.py
  algorithms/tests/test_manual_regeneration.py
  algorithms/tests/test_product_version_retention.py
  algorithms/tests/test_fujian_shadow_schedule.py
  algorithms/tests/test_retained_product_generator.py
)

for path in "${required_files[@]}"; do
  test -s "$path" || { printf 'missing RP-044/RP-045 file: %s\n' "$path" >&2; exit 1; }
done

bash -n scripts/regenerate_forecasts.sh
rg --quiet -- '--header @-' scripts/regenerate_forecasts.sh
if rg --quiet -- '--header "Authorization:' scripts/regenerate_forecasts.sh; then
  printf 'admin token is exposed through a curl argument\n' >&2
  exit 1
fi
rg --quiet 'run_retained_product_generator.py' scripts/regenerate_forecasts.sh
rg --quiet 'RAINPULSE_DERIVED_PRODUCT_KEEP_VERSIONS' scripts/regenerate_forecasts.sh
rg --quiet 'backfill_fujian_nowcastnet_shadow_5min.py' scripts/regenerate_forecasts.sh
rg --quiet -- "--output-root '\{staging_root\}'" scripts/regenerate_forecasts.sh
rg --quiet '^    RegenerationRequest:' contracts/openapi.yaml
rg --quiet 'manual-regeneration/' services/control/internal/orchestration/service.go
rg --quiet 'outsideForecastLookback' services/control/cmd/orchestrator/planner.go
rg --quiet -- '--force' scripts/backfill_historical_steps.py
rg --quiet 'prune_cycle_versions' scripts/backfill_fujian_nowcastnet_shadow.py
rg --quiet 'DROP CONSTRAINT IF EXISTS nowcast_input_runs_issue_time_grid_id_preprocess_version_ga_key' \
  deploy/postgres/migrations/0016_manual_regeneration.sql
rg --quiet 'CREATE INDEX nowcast_input_runs_lineage_idx' \
  deploy/postgres/migrations/0016_manual_regeneration.sql
rg --quiet 'CREATE TABLE pipeline_regeneration_requests' \
  deploy/postgres/migrations/0017_full_pipeline_regeneration.sql
rg --quiet "'QC_RUNNING'.*'GRID_RUNNING'.*'MOSAIC_RUNNING'" \
  deploy/postgres/migrations/0017_full_pipeline_regeneration.sql
rg --quiet 'regeneration_request_id' \
  deploy/postgres/migrations/0017_full_pipeline_regeneration.sql

if REGEN_PRESET=unsupported bash scripts/regenerate_forecasts.sh >/dev/null 2>&1; then
  printf 'unsupported regeneration preset unexpectedly succeeded\n' >&2
  exit 1
fi

printf 'RP-044 bounded regeneration and RP-045 retention checks passed\n'

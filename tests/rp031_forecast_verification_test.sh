#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  configs/schemas/operational-verification-profile.schema.json
  configs/verification/rp031-operational-deterministic-v1.yaml
  contracts/data/forecast-verification-result.md
  contracts/events/forecast-verification-requested.schema.json
  deploy/postgres/migrations/0015_forecast_verification.sql
  algorithms/rainpulse_algo/verification/operational.py
  algorithms/rainpulse_algo/verification/worker.py
  services/control/internal/postgres/forecast_verification_store.go
  services/control/internal/workspace/handler.go
  apps/web/src/workspace/MainWorkspace.tsx
)

for path in "${required_files[@]}"; do
  test -s "$path" || { printf 'missing RP-031 file: %s\n' "$path" >&2; exit 1; }
done

rg --quiet 'RAINPULSE_WORKER_PROFILE: forecast-verification' deploy/docker-compose.yaml
rg --quiet 'forecast.verification.requested.v1' contracts/events/forecast-verification-requested.schema.json
rg --quiet 'CREATE TABLE forecast_verification_runs' deploy/postgres/migrations/0015_forecast_verification.sql
rg --quiet 'GetVerificationSummary' services/control/internal/api/handler.go
rg --quiet 'addObservedTimeline' services/control/internal/workspace/handler.go
rg --quiet "verification: '检验回放'" apps/web/src/workspace/MainWorkspace.tsx

printf 'RP-031 automatic forecast-verification artifacts are present.\n'

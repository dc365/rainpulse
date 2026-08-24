#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  configs/qc/rp008-basic-v1.yaml
  configs/schemas/radar-qc.schema.json
  algorithms/rainpulse_algo/radar/qc.py
  algorithms/rainpulse_algo/radar/qc_worker.py
  algorithms/rainpulse_algo/radar/qc_zarr.py
  deploy/postgres/migrations/0007_radar_qc.sql
  scripts/radar_qc_smoke_test.sh
)

for path in "${required_files[@]}"; do
  [[ -f "$path" ]] || {
    printf 'missing RP-008 file: %s\n' "$path" >&2
    exit 1
  }
done

rg --quiet 'rainpulse.qc-radar-volume' algorithms/rainpulse_algo/radar/qc_zarr.py
rg --quiet 'radar-qc-basic' algorithms/rainpulse_algo/worker/handlers.py
rg --quiet '^  radar-qc-worker:' deploy/docker-compose.yaml
rg --quiet 'CREATE TABLE radar_qc_metrics' deploy/postgres/migrations/0007_radar_qc.sql
rg --quiet 'radar.qc.requested.v1' services/control/internal/orchestration/events.go

printf 'RP-008 basic polar QC structure checks passed\n'

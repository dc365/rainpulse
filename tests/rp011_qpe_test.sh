#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/schemas/qpe-profile.schema.json"
  "configs/qpe/rp011-basic-zr-v1.yaml"
  "contracts/data/radar-analysis.md"
  "contracts/events/analysis-qpe-requested.schema.json"
  "algorithms/rainpulse_algo/radar/qpe.py"
  "algorithms/rainpulse_algo/radar/qpe_profile.py"
  "algorithms/rainpulse_algo/radar/analysis_zarr.py"
  "algorithms/rainpulse_algo/radar/qpe_worker.py"
  "algorithms/tests/test_qpe.py"
  "deploy/postgres/migrations/0010_analysis_qpe.sql"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-011 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

grep -q 'RAINPULSE_WORKER_PROFILE: analysis-qpe-basic' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'AnalysisQPEJobType' \
  "$repo_root/services/control/internal/orchestration/events.go"

printf 'RP-011 basic QPE artifacts are present.\n'

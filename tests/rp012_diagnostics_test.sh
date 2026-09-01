#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/schemas/diagnostic-profile.schema.json"
  "configs/diagnostics/rp012-operational-diagnostics-v1.yaml"
  "contracts/data/diagnostic-bundle.md"
  "contracts/events/analysis-diagnostics-requested.schema.json"
  "algorithms/rainpulse_algo/diagnostics/profile.py"
  "algorithms/rainpulse_algo/diagnostics/renderer.py"
  "algorithms/rainpulse_algo/diagnostics/worker.py"
  "algorithms/tests/test_diagnostics.py"
  "deploy/postgres/migrations/0011_analysis_diagnostics.sql"
  "services/control/internal/workspace/handler.go"
  "apps/web/src/workspace/MainWorkspace.tsx"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-012 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

grep -q 'RAINPULSE_WORKER_PROFILE: analysis-diagnostics' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'AnalysisDiagnosticsJobType' \
  "$repo_root/services/control/internal/orchestration/events.go"
grep -q 'analysis-diagnostics' \
  "$repo_root/services/control/internal/workspace/handler.go"
grep -q "panel.role === 'diagnostic'" \
  "$repo_root/apps/web/src/workspace/MainWorkspace.tsx"

printf 'RP-012 analysis diagnostic artifacts are present.\n'

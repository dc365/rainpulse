#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "contracts/openapi.yaml"
  "contracts/events/job-requested.schema.json"
  "contracts/events/job-completed.schema.json"
  "contracts/events/radar-scan-received.schema.json"
  "contracts/events/radar-decode-requested.schema.json"
  "contracts/events/radar-qc-requested.schema.json"
  "contracts/events/radar-grid-requested.schema.json"
  "contracts/events/analysis-cycle-opened.schema.json"
  "contracts/events/analysis-mosaic-requested.schema.json"
  "contracts/events/nowcast-input-requested.schema.json"
  "contracts/events/nowcast-input-ready.schema.json"
  "contracts/events/forecast-run-requested.schema.json"
  "contracts/data/raw-radar-asset.md"
  "contracts/data/normalized-radar-volume.md"
  "contracts/data/qc-radar-volume.md"
  "contracts/data/radar-grid.md"
  "contracts/data/radar-analysis.md"
  "contracts/data/nowcast-input.md"
  "contracts/data/forecast-output.md"
  "contracts/oapi-codegen.yaml"
  "services/control/internal/api/generated/server.gen.go"
  "apps/web/src/api/generated/schema.ts"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-002 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

printf 'RP-002 data and event contract artifacts are present.\n'

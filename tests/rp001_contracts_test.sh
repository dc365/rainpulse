#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "contracts/openapi.yaml"
  "contracts/events/job-requested.schema.json"
  "contracts/events/job-completed.schema.json"
  "contracts/events/product-published.schema.json"
  "contracts/examples/job-requested.json"
  "contracts/examples/job-completed.json"
  "contracts/examples/product-published.json"
  "contracts/data/nowcast-input.md"
  "contracts/data/forecast-output.md"
  "contracts/oapi-codegen.yaml"
  "services/control/internal/api/generated/server.gen.go"
  "apps/web/src/api/generated/schema.ts"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-001 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

printf 'RP-001 contract artifacts are present.\n'

#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/schemas/radar-config.schema.json"
  "configs/schemas/grid-config.schema.json"
  "configs/schemas/ancillary-source.schema.json"
  "configs/radars/radar-inventory-template.yaml"
  "configs/radars/README.md"
  "configs/grids/fuzhou-0p01deg-v1.yaml"
  "configs/ancillary/fujian-taiwan-v1.yaml"
  "configs/qc/flag-definitions.yaml"
  "configs/tests/test_radar_config.py"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-001 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

printf 'RP-001 radar inventory and configuration artifacts are present.\n'

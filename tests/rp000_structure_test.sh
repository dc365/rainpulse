#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_directories=(
  "apps/web"
  "services/control"
  "algorithms/rainpulse_algo"
  "algorithms/workers"
  "contracts/events"
  "contracts/data"
  "configs"
  "deploy"
  "scripts"
)

required_files=(
  "Makefile"
  "README.md"
  "go.work"
  "pnpm-workspace.yaml"
  "algorithms/pyproject.toml"
  "deploy/docker-compose.yaml"
)

for relative_path in "${required_directories[@]}"; do
  if [[ ! -d "$repo_root/$relative_path" ]]; then
    printf 'missing required directory: %s\n' "$relative_path" >&2
    exit 1
  fi
done

for relative_path in "${required_files[@]}"; do
  if [[ ! -f "$repo_root/$relative_path" ]]; then
    printf 'missing required file: %s\n' "$relative_path" >&2
    exit 1
  fi
done

printf 'RP-000 repository structure is present.\n'

#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
generated_go="$repo_root/services/control/internal/api/generated/server.gen.go"
generated_ts="$repo_root/apps/web/src/api/generated/schema.ts"
check_dir=$(mktemp -d)
trap 'rm -rf "$check_dir"' EXIT

RAINPULSE_GO_CONTRACT_OUTPUT="$check_dir/server.gen.go" \
RAINPULSE_TS_CONTRACT_OUTPUT="$check_dir/schema.ts" \
  bash "$repo_root/scripts/generate_contracts.sh"

if ! cmp -s "$generated_go" "$check_dir/server.gen.go"; then
  diff -u "$generated_go" "$check_dir/server.gen.go" || true
  printf 'Go API types are stale; run make contracts-generate.\n' >&2
  exit 1
fi

if ! cmp -s "$generated_ts" "$check_dir/schema.ts"; then
  diff -u "$generated_ts" "$check_dir/schema.ts" || true
  printf 'TypeScript API types are stale; run make contracts-generate.\n' >&2
  exit 1
fi

printf 'Generated API types match contracts/openapi.yaml.\n'

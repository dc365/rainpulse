#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
go_output=${RAINPULSE_GO_CONTRACT_OUTPUT:-"$repo_root/services/control/internal/api/generated/server.gen.go"}
ts_output=${RAINPULSE_TS_CONTRACT_OUTPUT:-"$repo_root/apps/web/src/api/generated/schema.ts"}

mkdir -p "$(dirname "$go_output")" "$(dirname "$ts_output")"

cd "$repo_root"
go run github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@v2.8.0 \
  -config contracts/oapi-codegen.yaml \
  -o "$go_output" \
  contracts/openapi.yaml
pnpm exec openapi-typescript contracts/openapi.yaml --output "$ts_output"

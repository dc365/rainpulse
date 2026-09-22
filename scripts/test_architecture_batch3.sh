#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# This is full-checkout acceptance. Missing dependencies are errors, not passes.
python3 scripts/check_architecture_boundaries.py
bash scripts/go_control.sh test ./internal/readquery ./internal/api ./internal/workspace ./internal/apiapp
uv run --project algorithms python -m pytest \
  tests/architecture_batch3 \
  algorithms/tests/test_rfi_objects_v3.py algorithms/tests/test_residual_v61_repair.py
pnpm --filter @rainpulse/web exec vitest run \
  src/workspace/refreshPolicy.test.ts \
  src/workspace/useWorkspaceData.test.tsx \
  src/workspace/useWorkspaceData.batch3.test.tsx \
  src/App.batch3.test.tsx
pnpm --filter @rainpulse/web exec tsc -b --pretty false

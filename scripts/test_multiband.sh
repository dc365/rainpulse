#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# Actual repository dependencies, including the control-plane adapter: no stubs.
bash scripts/go_control.sh test ./internal/multiband ./internal/operations ./internal/controlplane ./internal/apiapp
uv run --project algorithms python -m pytest algorithms/tests/multiband algorithms/tests/test_operations_engine.py tests/multiband -q
pnpm --filter @rainpulse/web exec tsc -b --pretty false
pnpm --filter @rainpulse/web exec vitest run src/admin/AdminApp.test.tsx src/admin/Multiband.test.tsx src/admin/Monitoring.test.tsx
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
pnpm --filter @rainpulse/web exec tsc src/admin/model.ts --ignoreConfig --strict --target ES2022 --lib ES2022,DOM --module commonjs --outDir "$scratch" --pretty false
node tests/multiband/model.test.cjs "$scratch/model.js"
if [[ "${RAINPULSE_OPS_ALLOW_INTEGRATION:-}" == 1 && -n "${RAINPULSE_OPS_TEST_DATABASE_URL:-}" ]]; then
  GOWORK=off go -C services/control test -tags=integration ./internal/operations -run TestOpsPostgresMultiBandMigration -count=1 -v
else
  echo 'SKIP PostgreSQL migration integration: disposable database and explicit opt-in required.'
fi

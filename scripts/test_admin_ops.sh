#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# Compile against the real repository dependencies, not substitute domain types.
bash scripts/go_control.sh test ./internal/operations ./internal/apiapp ./internal/controlplane ./internal/postgres ./internal/webgateway
uv run --project algorithms python -m pytest algorithms/tests/test_operations_engine.py tests/operations -q
pnpm --filter @rainpulse/web exec tsc -b --pretty false
pnpm --filter @rainpulse/web exec vitest run src/admin/AdminApp.test.tsx src/admin/Monitoring.test.tsx src/App.batch3.test.tsx
node scripts/test_admin_model.cjs
if [[ "${RAINPULSE_OPS_ALLOW_INTEGRATION:-}" == 1 && -n "${RAINPULSE_OPS_TEST_DATABASE_URL:-}" ]]; then
  # Existing BDP source flags are not needed by this dependency-isolated package.
  GOWORK=off go -C services/control test -tags=integration ./internal/operations -run TestOpsPostgres -count=1 -v
else
  echo 'SKIP PostgreSQL integration: disposable test DSN and explicit opt-in are not configured.'
fi

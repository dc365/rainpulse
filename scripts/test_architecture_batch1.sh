#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# Preserve the repository's own BDP-aware Go toolchain entry.
bash scripts/go_control.sh test ./internal/planning ./internal/releaseguard \
  ./internal/controlplane ./internal/postgres ./internal/apiapp
# No dependency installation or network access is performed by this script.
python_bin="${RAINPULSE_TEST_PYTHON:-$root/algorithms/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then python_bin="$(command -v python3)"; fi
PYTHONPATH="$root/algorithms${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" -m pytest -q \
  algorithms/tests/test_completion_summary_contract.py \
  algorithms/tests/test_worker_release_identity.py \
  tests/architecture_batch1
if [[ -z "${RAINPULSE_TEST_DATABASE_URL:-}" ]]; then
  echo "NOTICE: real PostgreSQL integration tests were SKIPPED; set RAINPULSE_TEST_DATABASE_URL to an isolated test DB."
fi
# Validate real Compose separately: it needs the adopted manifest and existing
# deployment .env; do not guess or create production config while running tests.
echo "Source tests complete. Docker/NATS/server acceptance is separate; see docs/ARCHITECTURE_BATCH1_20260922.md."

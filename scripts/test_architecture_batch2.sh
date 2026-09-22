#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# Use the project's prepared environment. This command is for a complete checkout.
# SDK/database skips remain visible and are not an acceptance pass.
export PYTHONPATH="$root/algorithms:$root/scripts${PYTHONPATH:+:$PYTHONPATH}"
if [[ "${RAINPULSE_BATCH2_REQUIRE_INTEGRATION:-0}" == "1" ]]; then
  : "${RAINPULSE_TEST_DATABASE_URL:?use an isolated PostgreSQL test database}"
  : "${RAINPULSE_TEST_NATS_URL:?use an isolated NATS test server}"
  python -c 'import minio, nats, zarr'
fi
python -m pytest -q -ra tests/architecture_batch2 tests/architecture_batch1/test_rainpulsectl.py
bash scripts/go_control.sh test ./internal/workloads ./internal/releaseguard ./internal/messaging ./internal/controlplane ./internal/postgres ./internal/apiapp -count=1
printf '%s\n' 'Also run existing Worker/QC/contracts regressions and actual Compose resources-check before activation.'

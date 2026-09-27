#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Use the immutable pre-CD Git tree so CI does not need a private delivery ZIP.
if [[ -z "${RAINPULSE_CD_REFERENCE_ROOT:-}" ]]; then
  reference="$(mktemp -d)"
  trap 'rm -rf "$reference"' EXIT
  git archive 4c87cc83dc0036b303d904c108d292894dd39f28 algorithms | tar -x -C "$reference"
  export RAINPULSE_CD_REFERENCE_ROOT="$reference"
fi
PYTHONPATH="algorithms${PYTHONPATH:+:$PYTHONPATH}" RAINPULSE_PERFORMANCE_TELEMETRY=0 \
  python -m pytest -q algorithms/tests/performance_cd_20260927 "$@"

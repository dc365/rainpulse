#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/algorithms${PYTHONPATH:+:$PYTHONPATH}"
python -m pytest algorithms/tests/performance_batch1_20260925 "$@"

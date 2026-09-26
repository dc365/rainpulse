#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/algorithms${PYTHONPATH:+:$PYTHONPATH}"
python -m pytest algorithms/tests/performance_ab_20260926 "$@"

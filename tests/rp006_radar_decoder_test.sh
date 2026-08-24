#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  configs/radars/z9598.yaml
  algorithms/rainpulse_algo/radar/config.py
  algorithms/rainpulse_algo/radar/fmt.py
  algorithms/rainpulse_algo/radar/zarr_volume.py
  algorithms/rainpulse_algo/radar/worker.py
  algorithms/tests/test_fmt_decoder.py
  scripts/radar_decode_smoke_test.sh
)

for path in "${required_files[@]}"; do
  [[ -f "$path" ]] || {
    printf 'missing RP-006 file: %s\n' "$path" >&2
    exit 1
  }
done

rg --quiet 'DECODER_VERSION = "cma-rstm-2.0.0"' algorithms/rainpulse_algo/radar/fmt.py
rg --quiet 'geometry_encoding.*sweep_groups_v1|GEOMETRY_ENCODING = "sweep_groups_v1"' algorithms/rainpulse_algo/radar/zarr_volume.py
rg --quiet 'raw_reserved_codes' algorithms/rainpulse_algo/radar/zarr_volume.py
rg --quiet 'radar-decode-fmt' algorithms/rainpulse_algo/worker/handlers.py
rg --quiet 'radar-decode-worker:' deploy/docker-compose.yaml
rg --quiet '/data/Weather/RADA/FMT_L2_Z959X_SBD:ro' deploy/docker-compose.yaml

printf 'RP-006 real radar decoder structure checks passed\n'

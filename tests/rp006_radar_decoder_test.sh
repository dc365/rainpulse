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

# The worker rejects requests with any other decoder version. Check the
# control/compute boundary rather than pinning this check to an old release.
decoder_version=$(awk -F '"' '/^DECODER_VERSION = / { print $2 }' algorithms/rainpulse_algo/radar/fmt.py)
requested_version=$(awk -F '"' '/RadarDecoderVersion[[:space:]]*=/ { print $2 }' services/control/internal/orchestration/events.go)
[[ -n "$decoder_version" && "$decoder_version" == "$requested_version" ]] || {
  printf 'control decoder %s differs from worker %s\n' "$requested_version" "$decoder_version" >&2
  exit 1
}
rg --quiet 'geometry_encoding.*sweep_groups_v1|GEOMETRY_ENCODING = "sweep_groups_v1"' algorithms/rainpulse_algo/radar/zarr_volume.py
rg --quiet 'raw_reserved_codes' algorithms/rainpulse_algo/radar/zarr_volume.py
rg --quiet 'radar-decode-fmt' algorithms/rainpulse_algo/worker/handlers.py
rg --quiet 'radar-decode-worker:' deploy/docker-compose.yaml
rg --quiet 'RAINPULSE_RADAR_DATA_ROOT' deploy/docker-compose.yaml
rg --quiet 'create_host_path: false' deploy/docker-compose.yaml
[[ $(rg -c 'RAINPULSE_RADAR_CONFIG_DIR:.*RAINPULSE_RADAR_CONFIG_DIR' deploy/docker-compose.yaml) -eq 2 ]]

printf 'RP-006 real radar decoder structure checks passed\n'

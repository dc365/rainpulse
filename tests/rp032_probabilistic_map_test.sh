#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  contracts/data/algorithm-verification-probabilistic-map-bundle.md
  algorithms/rainpulse_algo/verification/map_bundle.py
  algorithms/rainpulse_algo/verification/mrms_nowcastnet_hindcast.py
  apps/web/src/EnsembleVerificationMapMatrix.tsx
  docs/RP032_NowcastNet空间地图包实施记录.md
)

for path in "${required_files[@]}"; do
  test -s "$path" || { printf 'missing RP-032 file: %s\n' "$path" >&2; exit 1; }
done

rg --quiet 'build_probabilistic_verification_map_bundle' algorithms/rainpulse_algo/verification/map_bundle.py
rg --quiet 'map_bundle_count' algorithms/rainpulse_algo/verification/mrms_nowcastnet_hindcast.py
rg --quiet 'NowcastNet 集合均值' apps/web/src/EnsembleVerificationMapMatrix.tsx
rg --quiet 'operational_eligible.*false|operational_eligible` is always `false`' contracts/data/algorithm-verification-probabilistic-map-bundle.md

printf 'RP-032 probabilistic spatial-map artifacts are present.\n'

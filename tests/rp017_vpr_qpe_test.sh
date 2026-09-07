#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/qpe/rp017-stratiform-vpr-v1.yaml"
  "configs/verification/rp017-vpr-shadow-replay-manifest-v1.json"
  "algorithms/rainpulse_algo/radar/vpr.py"
  "algorithms/rainpulse_algo/radar/vpr_shadow_replay.py"
  "contracts/data/radar-analysis.md"
  "algorithms/tests/test_qpe.py"
  "algorithms/tests/test_vpr_shadow_replay.py"
  "deploy/docker-compose.realtime-shadow.yaml"
  "deploy/.env.example"
  "scripts/validate_rp017_vpr_shadow_replay.py"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-017 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

grep -q 'RAINPULSE_PIPELINE_QPE_CONFIG' \
  "$repo_root/deploy/docker-compose.realtime-shadow.yaml"
grep -q 'RAINPULSE_QC_FLAG_DEFINITIONS' \
  "$repo_root/deploy/docker-compose.realtime-shadow.yaml"
grep -q 'DBZH_VPR_CORRECTED' \
  "$repo_root/contracts/data/radar-analysis.md"
grep -q 'rp017-vpr-shadow-replay-v1' \
  "$repo_root/configs/verification/rp017-vpr-shadow-replay-manifest-v1.json"
grep -q 'run_vpr_shadow_replay_validation' \
  "$repo_root/scripts/validate_rp017_vpr_shadow_replay.py"

printf 'RP-017 stratiform VPR QPE artifacts are present.\n'
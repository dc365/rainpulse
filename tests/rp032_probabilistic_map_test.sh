#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  contracts/data/algorithm-verification-probabilistic-map-bundle.md
  algorithms/rainpulse_algo/verification/map_bundle.py
  algorithms/rainpulse_algo/verification/mrms_nowcastnet_hindcast.py
  apps/web/src/EnsembleVerificationMapMatrix.tsx
  scripts/stage_probabilistic_verification_map_run.sh
  docs/RP032_NowcastNet空间地图包实施记录.md
)

for path in "${required_files[@]}"; do
  test -s "$path" || { printf 'missing RP-032 file: %s\n' "$path" >&2; exit 1; }
done

rg --quiet 'build_probabilistic_verification_map_bundle' algorithms/rainpulse_algo/verification/map_bundle.py
rg --quiet 'map_bundle_count' algorithms/rainpulse_algo/verification/mrms_nowcastnet_hindcast.py
rg --quiet 'NowcastNet 集合均值' apps/web/src/EnsembleVerificationMapMatrix.tsx
rg --quiet 'operational_eligible.*false|operational_eligible` is always `false`' contracts/data/algorithm-verification-probabilistic-map-bundle.md
rg --quiet 'map asset digest differs' scripts/stage_probabilistic_verification_map_run.sh

fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
FIXTURE_ROOT="$fixture" python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

root = Path(os.environ["FIXTURE_ROOT"])
source = root / "source"
manifest_root = source / "maps" / "wet-case" / "20250801T000000Z"
manifest_root.mkdir(parents=True)
asset = b"synthetic-png"
(manifest_root / "truth.png").write_bytes(asset)
(manifest_root / "manifest.json").write_text(json.dumps({
    "verification_profile_version": "rp032-test-v1",
    "operational_eligible": False,
    "layers": [{
        "object_path": "truth.png",
        "size_bytes": len(asset),
        "sha256": hashlib.sha256(asset).hexdigest(),
    }],
}))
(source / "maps" / "index.json").write_text(json.dumps({
    "verification_profile_version": "rp032-test-v1",
    "renderer_version": "renderer-test-v1",
    "bundle_count": 1,
    "layer_count": 1,
    "issues": [{
        "manifest_path": "wet-case/20250801T000000Z/manifest.json",
        "layer_count": 1,
    }],
}))
(source / "summary.json").write_text(json.dumps({
    "profile_version": "rp032-test-v1",
    "operational_eligible": False,
    "product_publication_enabled": False,
    "completed_issue_count": 1,
    "failed_issue_count": 0,
    "map_bundle_count": 1,
    "map_layer_count": 1,
    "map_renderer_version": "renderer-test-v1",
}))
PY

scripts/stage_probabilistic_verification_map_run.sh \
  "$fixture/source" "$fixture/target" holdout-map-test >/dev/null
test -f "$fixture/target/rp032-test-v1/holdout-map-test/maps/index.json"
if scripts/stage_probabilistic_verification_map_run.sh \
  "$fixture/source" "$fixture/target" holdout-map-test >/dev/null 2>&1; then
  echo "RP-032 staging unexpectedly overwrote an immutable target" >&2
  exit 1
fi

printf 'RP-032 probabilistic spatial-map artifacts are present.\n'

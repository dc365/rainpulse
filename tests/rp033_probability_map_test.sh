#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  contracts/data/algorithm-verification-probability-map-bundle.md
  configs/verification/algorithm-probability-map-v1.yaml
  algorithms/rainpulse_algo/verification/probability_map_bundle.py
  services/control/internal/workspace/handler.go
  apps/web/src/workspace/MainWorkspace.tsx
  docs/RP033_超阈值概率空间地图实施记录.md
)

for path in "${required_files[@]}"; do
  test -s "$path" || { printf 'missing RP-033 file: %s\n' "$path" >&2; exit 1; }
done

rg --quiet 'raw_ensemble_relative_frequency_uncalibrated' algorithms/rainpulse_algo/verification/probability_map_bundle.py
rg --quiet 'probability-map-frame' contracts/openapi.yaml
rg --quiet 'probability_exceedance' services/control/internal/workspace/handler.go
rg --quiet 'panel.data_kind.*probability_exceedance' apps/web/src/workspace/MainWorkspace.tsx
rg --quiet 'probability map asset digest differs' scripts/stage_probabilistic_verification_map_run.sh
rg --quiet 'operational_eligible.*false|operational_eligible` is always `false`' contracts/data/algorithm-verification-probability-map-bundle.md

fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
FIXTURE_ROOT="$fixture" python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

root = Path(os.environ["FIXTURE_ROOT"])
source = root / "source"
asset = b"synthetic-png"
digest = hashlib.sha256(asset).hexdigest()

def write_bundle(name, layers, renderer):
    issue = source / name / "wet-case" / "20250801T000000Z"
    issue.mkdir(parents=True)
    manifest_layers = []
    for layer in layers:
        path = issue / layer["object_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(asset)
        manifest_layers.append({
            **layer,
            "size_bytes": len(asset),
            "sha256": digest,
        })
    manifest = {
        "verification_profile_version": "rp033-test-v1",
        "operational_eligible": False,
        "layers": manifest_layers,
    }
    if name == "probability-maps":
        manifest.update({
            "product_publication_enabled": False,
            "calibration_status": "raw_ensemble_relative_frequency_uncalibrated",
        })
    (issue / "manifest.json").write_text(json.dumps(manifest))
    (source / name / "index.json").write_text(json.dumps({
        "verification_profile_version": "rp033-test-v1",
        "renderer_version": renderer,
        "bundle_count": 1,
        "layer_count": len(layers),
        "issues": [{
            "manifest_path": "wet-case/20250801T000000Z/manifest.json",
            "layer_count": len(layers),
        }],
    }))

write_bundle("maps", [{"object_path": "layers/truth.png"}], "rate-renderer-v1")
probability_layers = []
for threshold in (1, 5, 10, 20, 50):
    for name in ("truth", "nowcastnet", "steps"):
        probability_layers.append({
            "object_path": f"layers/{threshold}-{name}.png",
            "lead_minutes": 10,
            "threshold_mm_h": threshold,
        })
write_bundle("probability-maps", probability_layers, "probability-renderer-v1")
(source / "summary.json").write_text(json.dumps({
    "profile_version": "rp033-test-v1",
    "operational_eligible": False,
    "product_publication_enabled": False,
    "completed_issue_count": 1,
    "failed_issue_count": 0,
    "lead_minutes": [10],
    "thresholds_mm_h": [1, 5, 10, 20, 50],
    "map_bundle_count": 1,
    "map_layer_count": 1,
    "map_renderer_version": "rate-renderer-v1",
    "probability_map_bundle_count": 1,
    "probability_map_layer_count": 15,
    "probability_map_renderer_version": "probability-renderer-v1",
}))
PY

scripts/stage_probabilistic_verification_map_run.sh \
  "$fixture/source" "$fixture/target" holdout-probability-map-test >/dev/null
test -f "$fixture/target/rp033-test-v1/holdout-probability-map-test/probability-maps/index.json"

printf 'RP-033 raw exceedance-probability GIS artifacts are present.\n'

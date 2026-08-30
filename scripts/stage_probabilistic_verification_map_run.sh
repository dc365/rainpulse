#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 SOURCE_RUN TARGET_REPORT_ROOT RUN_ID" >&2
  exit 2
fi

source_run=$1
target_report_root=$2
run_id=$3

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
[[ -d "$source_run" ]] || { echo "source run is not a directory" >&2; exit 1; }
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo "run id is invalid" >&2
  exit 1
}

profile_version=$(python3 - "$source_run" <<'PY'
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
index = json.loads((root / "maps" / "index.json").read_text(encoding="utf-8"))

valid = summary.get("operational_eligible") is False
valid = valid and summary.get("product_publication_enabled") is False
valid = valid and summary.get("failed_issue_count") == 0
valid = valid and summary.get("completed_issue_count") == summary.get("map_bundle_count")
valid = valid and summary.get("map_bundle_count") == index.get("bundle_count")
valid = valid and summary.get("map_layer_count") == index.get("layer_count")
valid = valid and summary.get("map_renderer_version") == index.get("renderer_version")
valid = valid and summary.get("profile_version") == index.get(
    "verification_profile_version"
)
issues = index.get("issues")
valid = valid and isinstance(issues, list) and len(issues) == index.get("bundle_count")
if not valid:
    raise SystemExit("probabilistic map run failed the offline publication boundary")

layer_count = 0
for issue in issues:
    manifest_path = PurePosixPath(str(issue.get("manifest_path", "")))
    if manifest_path.is_absolute() or ".." in manifest_path.parts:
        raise SystemExit("map manifest path is unsafe")
    manifest_file = root / "maps" / manifest_path
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("operational_eligible") is not False:
        raise SystemExit("map manifest is operationally eligible")
    if manifest.get("verification_profile_version") != summary["profile_version"]:
        raise SystemExit("map manifest profile identity differs")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or len(layers) != issue.get("layer_count"):
        raise SystemExit("map manifest layer count differs")
    for layer in layers:
        object_path = PurePosixPath(str(layer.get("object_path", "")))
        if object_path.is_absolute() or ".." in object_path.parts:
            raise SystemExit("map asset path is unsafe")
        asset = manifest_file.parent / object_path
        data = asset.read_bytes()
        if len(data) != layer.get("size_bytes"):
            raise SystemExit("map asset size differs")
        if hashlib.sha256(data).hexdigest() != layer.get("sha256"):
            raise SystemExit("map asset digest differs")
        layer_count += 1
if layer_count != index["layer_count"]:
    raise SystemExit("map index layer count differs")

probability_bundle_count = summary.get("probability_map_bundle_count", 0)
probability_layer_count = summary.get("probability_map_layer_count", 0)
probability_renderer = summary.get("probability_map_renderer_version", "")
if probability_bundle_count == 0:
    if probability_layer_count != 0 or probability_renderer:
        raise SystemExit("probability map summary counts differ")
else:
    probability_index = json.loads(
        (root / "probability-maps" / "index.json").read_text(encoding="utf-8")
    )
    valid = summary.get("completed_issue_count") == probability_bundle_count
    valid = valid and probability_index.get("bundle_count") == probability_bundle_count
    valid = valid and probability_index.get("layer_count") == probability_layer_count
    valid = valid and probability_index.get("renderer_version") == probability_renderer
    valid = valid and probability_index.get("verification_profile_version") == summary.get(
        "profile_version"
    )
    probability_issues = probability_index.get("issues")
    valid = valid and isinstance(probability_issues, list)
    valid = valid and len(probability_issues) == probability_bundle_count
    if not valid:
        raise SystemExit("probability map run failed the offline publication boundary")
    thresholds = {float(value) for value in summary.get("thresholds_mm_h", [])}
    lead_minutes = {int(value) for value in summary.get("lead_minutes", [])}
    probability_assets = 0
    for issue in probability_issues:
        manifest_path = PurePosixPath(str(issue.get("manifest_path", "")))
        if manifest_path.is_absolute() or ".." in manifest_path.parts:
            raise SystemExit("probability map manifest path is unsafe")
        manifest_file = root / "probability-maps" / manifest_path
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest.get("operational_eligible") is not False:
            raise SystemExit("probability map manifest is operationally eligible")
        if manifest.get("product_publication_enabled") is not False:
            raise SystemExit("probability map manifest enables publication")
        if manifest.get("calibration_status") != "raw_ensemble_relative_frequency_uncalibrated":
            raise SystemExit("probability map calibration boundary differs")
        if manifest.get("verification_profile_version") != summary["profile_version"]:
            raise SystemExit("probability map profile identity differs")
        layers = manifest.get("layers")
        if not isinstance(layers, list) or len(layers) != issue.get("layer_count"):
            raise SystemExit("probability map manifest layer count differs")
        for layer in layers:
            if float(layer.get("threshold_mm_h", -1)) not in thresholds:
                raise SystemExit("probability map threshold differs")
            if int(layer.get("lead_minutes", -1)) not in lead_minutes:
                raise SystemExit("probability map lead differs")
            object_path = PurePosixPath(str(layer.get("object_path", "")))
            if object_path.is_absolute() or ".." in object_path.parts:
                raise SystemExit("probability map asset path is unsafe")
            asset = manifest_file.parent / object_path
            data = asset.read_bytes()
            if len(data) != layer.get("size_bytes"):
                raise SystemExit("probability map asset size differs")
            if hashlib.sha256(data).hexdigest() != layer.get("sha256"):
                raise SystemExit("probability map asset digest differs")
            probability_assets += 1
    if probability_assets != probability_layer_count:
        raise SystemExit("probability map index layer count differs")

profile = summary.get("profile_version")
if not isinstance(profile, str) or not profile:
    raise SystemExit("profile version is invalid")
print(profile)
PY
)

[[ "$profile_version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo "profile version is invalid" >&2
  exit 1
}

profile_directory="$target_report_root/$profile_version"
target_directory="$profile_directory/$run_id"
[[ ! -e "$target_directory" ]] || { echo "target run already exists" >&2; exit 1; }
mkdir -p "$profile_directory"
temporary_directory=$(mktemp -d "$profile_directory/.${run_id}.XXXXXX")
trap 'rm -rf "$temporary_directory"' EXIT
if cp --help 2>&1 | grep -q -- '--reflink'; then
  cp -a --reflink=auto "$source_run"/. "$temporary_directory"/
else
  cp -a "$source_run"/. "$temporary_directory"/
fi
chmod -R a+rX "$temporary_directory"
mv "$temporary_directory" "$target_directory"
trap - EXIT

echo "$target_directory"

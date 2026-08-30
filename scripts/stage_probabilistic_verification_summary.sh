#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 SOURCE_SUMMARY TARGET_REPORT_ROOT RUN_ID" >&2
  exit 2
fi

source_summary=$1
target_report_root=$2
run_id=$3

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
[[ -f "$source_summary" ]] || { echo "source summary is not a regular file" >&2; exit 1; }
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo "run id is invalid" >&2
  exit 1
}

profile_version=$(python3 - "$source_summary" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    report = json.load(handle)

required_strings = ("schema_version", "profile_version", "calibration_status")
valid = all(isinstance(report.get(key), str) and report[key] for key in required_strings)
valid = valid and report.get("split") in {"development", "holdout"}
valid = valid and report.get("operational_eligible") is False
valid = valid and report.get("product_publication_enabled") is False
valid = valid and isinstance(report.get("completed_issue_count"), int) and report["completed_issue_count"] > 0
valid = valid and isinstance(report.get("failed_issue_count"), int) and report["failed_issue_count"] >= 0
valid = valid and isinstance(report.get("nowcastnet_member_count"), int) and report["nowcastnet_member_count"] > 0
valid = valid and isinstance(report.get("steps_member_count"), int) and report["steps_member_count"] > 0
valid = valid and isinstance(report.get("models"), list) and "nowcastnet" in report["models"] and "steps" in report["models"]
lead_bands = report.get("lead_band_summary", {})
valid = valid and all(
    isinstance(lead_bands.get(name, {}).get("lead_minutes"), list)
    and len(lead_bands[name]["lead_minutes"]) == 2
    for name in ("near", "far")
)
runtime = report.get("runtime", {})
valid = valid and isinstance(runtime.get("device_name"), str) and bool(runtime["device_name"])
if not valid:
    raise SystemExit("probabilistic summary failed the offline publication boundary")
print(report["profile_version"])
PY
)
[[ "$profile_version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo "profile version is invalid" >&2
  exit 1
}

target_directory="$target_report_root/$profile_version/$run_id"
mkdir -p "$target_directory"
temporary_summary=$(mktemp "$target_directory/.summary.json.XXXXXX")
trap 'rm -f "$temporary_summary"' EXIT
install -m 0644 "$source_summary" "$temporary_summary"
mv -f "$temporary_summary" "$target_directory/summary.json"
trap - EXIT

echo "$target_directory/summary.json"

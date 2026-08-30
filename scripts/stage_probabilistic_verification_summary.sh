#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 SOURCE_SUMMARY TARGET_REPORT_ROOT RUN_ID" >&2
  exit 2
fi

source_summary=$1
target_report_root=$2
run_id=$3

command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
[[ -f "$source_summary" ]] || { echo "source summary is not a regular file" >&2; exit 1; }
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo "run id is invalid" >&2
  exit 1
}

profile_version=$(jq -er '.profile_version' "$source_summary")
[[ "$profile_version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo "profile version is invalid" >&2
  exit 1
}

jq -e '
  (.schema_version | type == "string" and length > 0) and
  (.split == "development" or .split == "holdout") and
  (.calibration_status | type == "string" and length > 0) and
  (.operational_eligible == false) and
  (.product_publication_enabled == false) and
  (.completed_issue_count > 0) and
  (.failed_issue_count >= 0) and
  (.nowcastnet_member_count > 0) and
  (.steps_member_count > 0) and
  (.models | index("nowcastnet") != null) and
  (.models | index("steps") != null) and
  (.lead_band_summary.near.lead_minutes | length == 2) and
  (.lead_band_summary.far.lead_minutes | length == 2) and
  (.runtime.device_name | type == "string" and length > 0)
' "$source_summary" >/dev/null || {
  echo "probabilistic summary failed the offline publication boundary" >&2
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

#!/usr/bin/env bash
set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
preset=${REGEN_PRESET:-}
source_run_id=${REGEN_RUN_ID:-}
issue_time=${REGEN_ISSUE_TIME:-}
input_uri=${REGEN_INPUT_URI:-}
reason=${REGEN_REASON:-manual algorithm validation}
api_url=${REGEN_API_URL:-http://127.0.0.1:8080/api/v1}

usage() {
  printf '%s\n' \
    'Usage: make regenerate REGEN_PRESET=<forecast-all|pysteps-lk|pysteps-steps|nowcastnet|products> ...' \
    '  primary presets require REGEN_RUN_ID and RAINPULSE_ADMIN_TOKEN' \
    '  pysteps-steps requires REGEN_INPUT_URI and REGEN_ISSUE_TIME' \
    '  nowcastnet requires REGEN_ISSUE_TIME' \
    '  forecast-all requires all of the above'
}

require_value() {
  local name=$1
  local value=$2
  if [[ -z "$value" ]]; then
    printf 'missing %s\n' "$name" >&2
    usage >&2
    exit 2
  fi
}

trigger_primary() {
  local api_preset=$1
  require_value REGEN_RUN_ID "$source_run_id"
  local admin_token=${RAINPULSE_ADMIN_TOKEN:-}
  require_value RAINPULSE_ADMIN_TOKEN "$admin_token"
  if [[ "$admin_token" == *$'\r'* || "$admin_token" == *$'\n'* ]]; then
    printf 'RAINPULSE_ADMIN_TOKEN contains an invalid line break\n' >&2
    exit 2
  fi
  local response_file
  response_file=$(mktemp "${TMPDIR:-/tmp}/rainpulse-regeneration.XXXXXX")
  trap 'rm -f "$response_file"' EXIT
  local request_body
  request_body=$(
    python3 - "$api_preset" "$reason" <<'PY'
import json
import sys

print(json.dumps({"preset": sys.argv[1], "reason": sys.argv[2]}))
PY
  )
  local status
  status=$(printf 'Authorization: Bearer %s\n' "$admin_token" | \
    curl --silent --show-error \
      --output "$response_file" \
      --write-out '%{http_code}' \
      --request POST \
      --header 'Content-Type: application/json' \
      --header @- \
      --data-binary "$request_body" \
      "$api_url/admin/runs/$source_run_id/rerun"
  )
  if [[ "$status" != 202 ]]; then
    printf 'primary regeneration rejected (HTTP %s): ' "$status" >&2
    cat "$response_file" >&2
    printf '\n' >&2
    exit 1
  fi
  python3 - "$response_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
print(json.dumps({
    "stage": "primary",
    "run_id": payload["run_id"],
    "rerun_of": payload.get("rerun_of"),
    "status": payload["status"],
}, ensure_ascii=False))
PY
  rm -f "$response_file"
  trap - EXIT
}

run_steps() {
  require_value REGEN_INPUT_URI "$input_uri"
  require_value REGEN_ISSUE_TIME "$issue_time"
  (
    cd "$repository_root"
    uv run --project algorithms python scripts/backfill_historical_steps.py \
      --input-uri "$input_uri" \
      --issue-time "$issue_time" \
      --force \
      --output-root "${REGEN_STEPS_OUTPUT_ROOT:-runtime/products/ensemble}" \
      --grid-config "${REGEN_GRID_CONFIG:-configs/grids/fuzhou-0p01deg-v1.yaml}" \
      --lk-config "${REGEN_LK_CONFIG:-configs/nowcast/rp016-pysteps-lk-v1.yaml}" \
      --steps-config "${REGEN_STEPS_CONFIG:-configs/nowcast/rp022-pysteps-steps-v1.yaml}" \
      --product-config "${REGEN_ENSEMBLE_PRODUCT_CONFIG:-configs/products/rp023-ensemble-application-products-v1.yaml}"
  )
}

run_nowcastnet() {
  require_value REGEN_ISSUE_TIME "$issue_time"
  local python_binary=${REGEN_NOWCASTNET_PYTHON:-$repository_root/runtime/nowcastnet/venv/bin/python}
  if [[ ! -x "$python_binary" ]]; then
    printf 'NowcastNet Python is not executable: %s\n' "$python_binary" >&2
    exit 2
  fi
  "$python_binary" "$repository_root/scripts/backfill_fujian_nowcastnet_shadow.py" \
    --catalog-url "${REGEN_ANALYSIS_CATALOG_URL:-http://127.0.0.1:8080/api/v1/analysis-cycles?status=ANALYSIS_READY&limit=200}" \
    --output-root "${REGEN_NOWCASTNET_OUTPUT_ROOT:-$repository_root/runtime/products/nowcastnet}" \
    --grid-config "${REGEN_GRID_CONFIG:-$repository_root/configs/grids/fuzhou-0p01deg-v1.yaml}" \
    --model-config "${REGEN_NOWCASTNET_CONFIG:-$repository_root/configs/nowcast/rp026-nowcastnet-offline-v1.yaml}" \
    --product-config "${REGEN_PRODUCT_CONFIG:-$repository_root/configs/products/rp015-application-products-v1.yaml}" \
    --capsule-root "${REGEN_NOWCASTNET_CAPSULE_ROOT:-/opt/rainpulse/nowcastnet/official-v1}" \
    --device "${REGEN_NOWCASTNET_DEVICE:-cuda:0}" \
    --issue-time "$issue_time"
}

case "$preset" in
  forecast-all)
    trigger_primary forecast_all
    run_steps
    run_nowcastnet
    ;;
  pysteps-lk)
    trigger_primary pysteps_lk
    ;;
  products)
    trigger_primary products
    ;;
  pysteps-steps)
    run_steps
    ;;
  nowcastnet)
    run_nowcastnet
    ;;
  *)
    printf 'unsupported REGEN_PRESET: %s\n' "$preset" >&2
    usage >&2
    exit 2
    ;;
esac

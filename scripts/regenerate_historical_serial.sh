#!/usr/bin/env bash
# Regenerate historical forecast cycles without overlapping their shared radar
# input frames. Run this on the deployment host, as root (or via sudo).
set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
regeneration_date=${REGEN_DATE:?set REGEN_DATE as YYYY-MM-DD}
api_url=${REGEN_API_URL:-http://127.0.0.1:8080/api/v1}
poll_seconds=${REGEN_POLL_SECONDS:-20}

cd "$repository_root"
set -a
# shellcheck disable=SC1091
. deploy/.env
set +a
# The deployment host keeps the supported Python/uv toolchain under the
# service account.  The serial runner is started with sudo so it can inspect
# the local Postgres container; retain that toolchain explicitly.
export PATH="${REGEN_TOOLCHAIN_BIN:-/home/yons/.local/bin:/home/yons/miniconda3/bin}:$PATH"

log() {
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"
}

query() {
  docker exec rainpulse-postgres-1 psql -U rainpulse -d rainpulse -At -F $'\t' -c "$1"
}

issues=$(query "
SELECT DISTINCT ON (issue_time) run_id, issue_time
FROM forecast_runs
WHERE status = 'PUBLISHED'
  AND rerun_of IS NULL
  AND issue_time::date = '${regeneration_date}'
ORDER BY issue_time, created_at DESC")
total=$(printf '%s\n' "$issues" | sed '/^$/d' | wc -l | tr -d ' ')
if [[ "$total" == 0 ]]; then
  printf 'no published base runs found for %s\n' "$regeneration_date" >&2
  exit 2
fi

log "start serialized full-chain regeneration date=$regeneration_date cycles=$total"
index=0
while IFS=$'\t' read -r source_run issue_time; do
  [[ -n "$source_run" ]] || continue
  index=$((index + 1))
  row=$(query "
SELECT request_id, status
FROM pipeline_regeneration_requests
WHERE source_run_id = '${source_run}'
ORDER BY created_at DESC
LIMIT 1")
  request_id=${row%%$'\t'*}
  state=${row#*$'\t'}
  if [[ -z "$row" || "$state" == "FAILED" ]]; then
    response=$(mktemp)
    status=$(curl --silent --show-error --output "$response" --write-out '%{http_code}' \
      --request POST \
      --header 'Content-Type: application/json' \
      --header "Authorization: Bearer $RAINPULSE_ADMIN_TOKEN" \
      --data '{"preset":"forecast_all","reason":"serialized historical refresh"}' \
      "$api_url/admin/runs/$source_run/rerun" || true)
    if [[ "$status" != 202 ]]; then
      log "cycle=$index/$total issue=$issue_time submit_failed http=$status"
      rm -f "$response"
      continue
    fi
    rm -f "$response"
    row=$(query "
SELECT request_id, status
FROM pipeline_regeneration_requests
WHERE source_run_id = '${source_run}'
ORDER BY created_at DESC
LIMIT 1")
    request_id=${row%%$'\t'*}
    state=${row#*$'\t'}
    log "cycle=$index/$total issue=$issue_time submitted request=$request_id"
  else
    log "cycle=$index/$total issue=$issue_time resume request=$request_id state=$state"
  fi

  while true; do
    state=$(query "SELECT status FROM pipeline_regeneration_requests WHERE request_id = '${request_id}'")
    case "$state" in
      SUCCEEDED)
        log "cycle=$index/$total issue=$issue_time succeeded"
        break
        ;;
      FAILED)
        reason=$(query "SELECT coalesce(error_message, '') FROM pipeline_regeneration_requests WHERE request_id = '${request_id}'")
        log "cycle=$index/$total issue=$issue_time failed reason=$reason"
        break
        ;;
      *) sleep "$poll_seconds" ;;
    esac
  done
done <<< "$issues"

log "serialized full-chain regeneration complete date=$regeneration_date"

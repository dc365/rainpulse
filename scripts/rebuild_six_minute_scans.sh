#!/usr/bin/env bash
# Six-minute historical rebuild, stage 1: grid every QC-ready volume of a case
# date through the unified control binary.
#
# The historical QC-only batches deliberately keep their scans out of the
# automatic grid scheduler (store.IsQCOnlyScan).  This script only enqueues the
# grid stage explicitly; the replay window then drives mosaic -> QPE ->
# diagnostics -> NowcastInput -> pySTEPS-LK -> products on the six-minute clock.
#
# Run on the deployment host as the service user; no sudo and no database edits.
#   REBUILD_DATE_UTC=2026-08-28 scripts/rebuild_six_minute_scans.sh
#   REBUILD_DATE_UTC=2026-08-28 REBUILD_DRY_RUN=1 scripts/rebuild_six_minute_scans.sh
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
binary="${REBUILD_BINARY:-$root/.build/linux-amd64/rainpulse}"
date_utc=${REBUILD_DATE_UTC:-2026-08-28}
dry_run=${REBUILD_DRY_RUN:-0}
grid_config=${REBUILD_GRID_CONFIG:-}

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

pid=$(systemctl show rainpulse -p MainPID --value)
if [[ -z "$pid" || ! -r "/proc/$pid/environ" ]]; then
  printf 'rainpulse is not running under this account; start it first\n' >&2
  exit 2
fi
[[ -x "$binary" ]] || { printf 'missing control binary %s\n' "$binary" >&2; exit 2; }

# The service environment is the only complete runtime configuration
# (Postgres, NATS, object store, config paths).  Never print it.
while IFS= read -r -d '' setting; do
  export "$setting"
done < "/proc/$pid/environ"

: "${RAINPULSE_DATABASE_HOST:=127.0.0.1}"
: "${RAINPULSE_DATABASE_PORT:=5432}"
: "${RAINPULSE_DATABASE_NAME:=rainpulse}"
export PGPASSWORD="${RAINPULSE_DATABASE_PASSWORD:-}"
grid_config=${grid_config:-${RAINPULSE_PIPELINE_GRID_CONFIG:-}}
[[ -n "$grid_config" && -r "$grid_config" ]] || {
  printf 'missing readable grid config: %s\n' "$grid_config" >&2
  exit 2
}

query() {
  psql -h "$RAINPULSE_DATABASE_HOST" -p "$RAINPULSE_DATABASE_PORT" \
    -U rainpulse -d "$RAINPULSE_DATABASE_NAME" -At -F $'\t' -c "$1"
}

# A volume can be gridded whenever its QC volume exists, even after an earlier
# grid attempt failed; only volumes that already produced a grid are skipped.
read -r ready total <<<"$(query "
SELECT count(*) FILTER (WHERE r.status <> 'RADAR_GRID_READY' AND r.qc_uri IS NOT NULL),
       count(*)
FROM radar_scan_runs r JOIN radar_scans s ON s.scan_id = r.scan_id
WHERE s.volume_end_time >= '${date_utc}' AND s.volume_end_time < '${date_utc}'::date + 1")"
if [[ "$total" == 0 ]]; then
  printf 'no radar volumes found for %s\n' "$date_utc" >&2
  exit 2
fi
log "date=$date_utc volumes=$total pending_grid=$ready grid_config=$grid_config dry_run=$dry_run"

pending=$(query "
SELECT r.scan_id
FROM radar_scan_runs r JOIN radar_scans s ON s.scan_id = r.scan_id
WHERE s.volume_end_time >= '${date_utc}' AND s.volume_end_time < '${date_utc}'::date + 1
  AND r.status <> 'RADAR_GRID_READY' AND r.qc_uri IS NOT NULL
ORDER BY s.volume_end_time, s.radar_id")
index=0
queued=0
while IFS= read -r scan; do
  [[ -n "$scan" ]] || continue
  index=$((index + 1))
  if [[ "$dry_run" == 1 ]]; then
    if [[ $index -le 3 ]]; then
      log "dry-run grid $index/$ready scan=$scan"
    fi
    continue
  fi
  if "$binary" radar-grid "$scan" "$grid_config" >/dev/null 2>&1; then
    queued=$((queued + 1))
  else
    log "grid request failed scan=$scan"
  fi
  if (( index % 50 == 0 )); then
    log "queued $index/$ready"
  fi
done <<<"$pending"
log "grid requests queued=$queued/$ready"

if [[ "$dry_run" == 1 ]]; then
  exit 0
fi

# Wait for the grid workers so the replay window never sees a half-gridded slot.
while true; do
  read -r remaining <<<"$(query "
SELECT count(*) FROM radar_scan_runs r JOIN radar_scans s ON s.scan_id = r.scan_id
WHERE s.volume_end_time >= '${date_utc}' AND s.volume_end_time < '${date_utc}'::date + 1
  AND r.status <> 'RADAR_GRID_READY' AND r.qc_uri IS NOT NULL")"
  done_count=$(query "
SELECT count(*) FROM radar_scan_runs r JOIN radar_scans s ON s.scan_id = r.scan_id
WHERE s.volume_end_time >= '${date_utc}' AND s.volume_end_time < '${date_utc}'::date + 1
  AND r.status = 'RADAR_GRID_READY'")
  log "grid pending=$remaining done=$done_count"
  if [[ "$remaining" == 0 ]]; then
    break
  fi
  sleep "${REBUILD_POLL_SECONDS:-60}"
done
log "all $date_utc volumes are grid-ready; open the replay window next"

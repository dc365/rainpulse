#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
base=(docker compose --env-file "${RAINPULSE_DEPLOY_ENV_FILE:-$root/deploy/.env}" -f deploy/docker-compose.yaml -f deploy/docker-compose.realtime-shadow.yaml)
unified=("${base[@]}" -f deploy/docker-compose.unified.yaml)
"${unified[@]}" config --quiet
docker image inspect "rainpulse-cpu-worker:${RAINPULSE_CPU_IMAGE_TAG:-latest}" >/dev/null
[[ -f /etc/systemd/system/rainpulse.service ]] || { echo 'Configure rainpulse.service first.' >&2; exit 1; }
rollback(){
  trap - ERR
  systemctl stop rainpulse || true
  if docker container inspect rainpulse-api-1 rainpulse-web-1 rainpulse-orchestrator-1 rainpulse-radar-ingest-1 >/dev/null 2>&1; then
    "${base[@]}" start api web orchestrator radar-ingest
    echo 'Restored legacy Go containers. Data was not removed.' >&2
  else
    echo 'Unified service stopped; no legacy containers exist on this fresh host. Data was not removed.' >&2
  fi
}
if [[ "${1:-}" == '--rollback' ]]; then rollback; exit; fi
trap rollback ERR
"${base[@]}" stop radar-ingest orchestrator api web
"${unified[@]}" up -d --no-deps radar-decode-worker radar-qc-worker radar-grid-worker radar-mosaic-worker analysis-qpe-worker analysis-diagnostics-worker nowcast-input-worker pysteps-lk-worker product-builder-worker forecast-verification-worker nowcastnet-shadow-probe
systemctl restart rainpulse
ready=false
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:4173/api/v1/system/status | python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ready"' 2>/dev/null \
     && curl -fsS --max-time 2 http://127.0.0.1:8090/healthz >/dev/null \
     && curl -fsS --max-time 2 http://127.0.0.1:8092/status >/dev/null; then
    ready=true; break
  fi
  sleep 1
done
[[ "$ready" == true ]]
systemctl enable rainpulse
# Test simulation and host monitoring are no longer default runtime services.
"${base[@]}" --profile '*' stop simulation-worker prometheus alertmanager node-exporter
trap - ERR
echo 'Unified Go service ready on 4173; legacy containers retained stopped for rollback.'

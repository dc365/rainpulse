#!/usr/bin/env bash
# Deploy the reviewed QC patch on the unified 105 deployment. No raw data changes.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
stage=${1:?usage: sudo bash scripts/deploy_qc_z9598.sh ABSOLUTE_STAGE_DIRECTORY [--rollback]}
[[ "$stage" = /* ]] || stage="$root/$stage"
[[ $EUID == 0 ]] || { echo 'sudo is required to update the worker image and rainpulse.service.' >&2; exit 1; }
cd "$root"
compose=(docker compose --env-file deploy/.env -f deploy/docker-compose.yaml -f deploy/docker-compose.realtime-shadow.yaml -f deploy/docker-compose.unified.yaml)
backup="$stage/rollback"
restore() {
  systemctl stop rainpulse || true
  cp -p "$backup/rainpulse" .build/linux-amd64/rainpulse
  cp -p "$backup/shadow.yaml" deploy/docker-compose.realtime-shadow.yaml
  cp -p "$backup/qc.py" algorithms/rainpulse_algo/radar/qc.py
  cp -p "$backup/radar-qc.schema.json" configs/schemas/radar-qc.schema.json
  cp -p "$backup/control.env" /etc/rainpulse/control.env
  cp -p "$backup/rainpulse.service" /etc/systemd/system/rainpulse.service
  docker tag "$(cat "$backup/image-id")" rainpulse-cpu-worker:latest
  "${compose[@]}" up -d --no-deps --wait radar-qc-worker
  systemctl daemon-reload
  systemctl start rainpulse
}
if [[ ${2:-} == --rollback ]]; then restore; exit; fi
[[ ! -e "$stage/INSTALLED" ]] || { echo 'This reviewed patch is already installed.'; exit 0; }
(cd "$stage" && sha256sum -c SHA256SUMS)
python3 - "$root" "$stage/base-hashes.json" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
for name,expected in json.loads(pathlib.Path(sys.argv[2]).read_text()).items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
        raise SystemExit('Deployment changed since review: '+name)
PY
# Build against the actual current worker, without apt/network or unrelated code.
qc_container=$("${compose[@]}" ps -q radar-qc-worker)
[[ -n "$qc_container" ]]
active_qc=$(docker exec rainpulse-postgres-1 psql -U rainpulse -d rainpulse -At -c "SELECT count(*) FROM jobs WHERE job_type='radar.qc' AND status='RUNNING'")
[[ "$active_qc" == 0 ]] || { echo 'QC jobs are still running; retry deployment after they drain.' >&2; exit 1; }
old_image=$(docker inspect --format '{{.Image}}' "$qc_container")
[[ "$old_image" == "$(docker image inspect rainpulse-cpu-worker:latest --format '{{.Id}}')" ]] || {
  echo 'QC worker and shared latest image differ; reconcile deployment before applying.' >&2; exit 1;
}
docker tag "$old_image" rainpulse-cpu-worker:qc-z9598-rollback
# A separate tag is prepared before stopping the service.
docker build --network=none --pull=false -f "$stage/Dockerfile" -t rainpulse-cpu-worker:qc-z9598-2.1.0 "$stage"
mkdir -p "$backup"
chmod 700 "$backup"
cp -p .build/linux-amd64/rainpulse "$backup/rainpulse"
cp -p deploy/docker-compose.realtime-shadow.yaml "$backup/shadow.yaml"
cp -p algorithms/rainpulse_algo/radar/qc.py "$backup/qc.py"
cp -p configs/schemas/radar-qc.schema.json "$backup/radar-qc.schema.json"
cp -p /etc/rainpulse/control.env "$backup/control.env"
cp -p /etc/systemd/system/rainpulse.service "$backup/rainpulse.service"
printf '%s\n' "$old_image" > "$backup/image-id"
rollback_on_error() {
  trap - ERR
  echo 'Deployment failed; restoring the previous service/configuration/image.' >&2
  restore
  exit 1
}
trap rollback_on_error ERR
systemctl stop rainpulse
install -m 755 -o yons -g yons "$stage/rainpulse" .build/linux-amd64/rainpulse
install -m 644 -o yons -g yons "$stage/qc.py" algorithms/rainpulse_algo/radar/qc.py
install -m 644 -o yons -g yons "$stage/qc_polarimetric_radial.py" algorithms/rainpulse_algo/radar/qc_polarimetric_radial.py
install -m 644 -o yons -g yons "$stage/fujian-qc-evidence-v3.yaml" configs/qc/fujian-qc-evidence-v3.yaml
install -m 644 -o yons -g yons "$stage/radar-qc.schema.json" configs/schemas/radar-qc.schema.json
python3 - <<'PY'
from pathlib import Path
p=Path('deploy/docker-compose.realtime-shadow.yaml')
s=p.read_text()
old='/opt/rainpulse/configs/qc/fujian-qc-evidence-v2.yaml'
if s.count(old)!=2:
    raise SystemExit('Expected both control and QC worker to use the reviewed v2 profile')
p.write_text(s.replace(old,'/opt/rainpulse/configs/qc/fujian-qc-evidence-v3.yaml'))
PY
docker tag rainpulse-cpu-worker:qc-z9598-2.1.0 rainpulse-cpu-worker:latest
"${compose[@]}" up -d --no-deps --wait radar-qc-worker
python3 scripts/configure_unified.py --root "$root" --user yons
systemctl daemon-reload
systemctl start rainpulse
for attempt in {1..30}; do
  if curl --fail --silent --max-time 3 http://127.0.0.1:8090/healthz > /dev/null; then break; fi
  sleep 2
done
curl --fail --silent --max-time 3 http://127.0.0.1:8090/healthz > /dev/null
curl --fail --silent --max-time 5 'http://127.0.0.1:8080/api/v1/workspace/cycles?limit=1' > /dev/null
date -u +%FT%TZ > "$stage/INSTALLED"
trap - ERR
echo 'QC 2.1.0 and regeneration ownership fix installed; starting serialized refresh.'
# The runner stops at the first failed cycle; successful QC alone is not success.
sudo -u yons bash "$stage/launch-refresh.sh"

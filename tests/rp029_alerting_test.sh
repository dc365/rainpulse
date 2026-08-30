#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  deploy/alertmanager/Dockerfile
  deploy/observability/alertmanager.yaml
  deploy/observability/prometheus.yaml
  services/control/internal/alerting/client.go
  apps/web/src/AlertWorkspace.tsx
)

for path in "${required_files[@]}"; do
  test -f "$path" || { printf 'missing RP-029 file: %s\n' "$path" >&2; exit 1; }
done

rg --quiet '^  alertmanager:' deploy/docker-compose.yaml
rg --quiet 'quay.io/prometheus/alertmanager:v0.34.0' deploy/alertmanager/Dockerfile
rg --quiet 'RAINPULSE_PROMETHEUS_URL' deploy/docker-compose.yaml
rg --quiet 'RAINPULSE_ALERTMANAGER_URL' deploy/docker-compose.yaml
rg --quiet '^alerting:' deploy/observability/prometheus.yaml
rg --quiet 'alertmanager:9093' deploy/observability/prometheus.yaml
rg --quiet 'receiver: rainpulse-local' deploy/observability/alertmanager.yaml

if rg --quiet 'webhook_configs|email_configs|slack_configs|wechat_configs|msteams_configs' deploy/observability/alertmanager.yaml; then
  printf 'RP-029 must not enable external notifications before recipients are approved\n' >&2
  exit 1
fi

printf 'RP-029 alerting structure checks passed\n'

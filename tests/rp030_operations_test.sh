#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  services/control/internal/operationalissues/issues.go
  services/control/internal/postgres/operational_issues.go
  apps/web/src/workspace/AdminWorkspace.tsx
)

for path in "${required_files[@]}"; do
  test -f "$path" || { printf 'missing RP-030 file: %s\n' "$path" >&2; exit 1; }
done

rg --quiet '^  /operations/issues:' contracts/openapi.yaml
rg --quiet '^  node-exporter:' deploy/docker-compose.yaml
rg --quiet 'MINIO_PROMETHEUS_AUTH_TYPE: public' deploy/docker-compose.yaml
rg --quiet '/minio/v2/metrics/cluster' deploy/observability/prometheus.yaml
rg --quiet 'node-exporter:9100' deploy/observability/prometheus.yaml
rg --quiet 'rainpulse_job_active_seconds' deploy/observability/rainpulse-alerts.yaml
rg --quiet 'rainpulse_job_failure_timestamp_seconds' deploy/observability/rainpulse-alerts.yaml
rg --quiet 'rainpulse_outbox_event_pending_seconds' deploy/observability/rainpulse-alerts.yaml
rg --quiet 'minio_cluster_capacity_usable_free_bytes' deploy/observability/rainpulse-alerts.yaml
rg --quiet 'node_filesystem_avail_bytes' deploy/observability/rainpulse-alerts.yaml
rg --quiet '/api/v1/operations/issues' apps/web/src/workspace/AdminWorkspace.tsx
rg --quiet '运行问题' apps/web/src/workspace/AdminWorkspace.tsx

printf 'RP-030 operational correlation and capacity checks passed\n'

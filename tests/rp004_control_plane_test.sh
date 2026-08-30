#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  deploy/postgres/migrations/0002_control_plane.sql
  deploy/postgres/migrations/0003_completion_idempotency.sql
  deploy/postgres/migrations/0005_radar_workflows.sql
  services/control/cmd/orchestrator/main.go
  services/control/internal/workflow/state.go
  services/control/internal/workflow/radar.go
  services/control/internal/orchestration/service.go
  services/control/internal/messaging/jetstream.go
  services/control/internal/postgres/store.go
  services/control/internal/postgres/radar_store.go
  scripts/control_plane_smoke_test.sh
)

for path in "${required_files[@]}"; do
  test -f "$path" || { printf 'missing RP-004 foundation file: %s\n' "$path" >&2; exit 1; }
done

rg --quiet '^  orchestrator:' deploy/docker-compose.yaml
rg --quiet 'rainpulse.jobs.requested' contracts/events/README.md
rg --quiet 'rainpulse.jobs.completed' contracts/events/README.md
rg --quiet 'inbox_events' deploy/postgres/migrations/0002_control_plane.sql
rg --quiet 'UNIQUE INDEX.*inbox_events_job_event_type|inbox_events_job_event_type_uidx' deploy/postgres/migrations/0003_completion_idempotency.sql
rg --quiet 'service_healthy' deploy/docker-compose.yaml
rg --quiet 'rainpulse-orchestrator' Makefile
rg --quiet 'CREATE TABLE workflow_runs' deploy/postgres/migrations/0005_radar_workflows.sql
rg --quiet 'CREATE TABLE radar_scan_runs' deploy/postgres/migrations/0005_radar_workflows.sql
rg --quiet 'CREATE TABLE analysis_cycles' deploy/postgres/migrations/0005_radar_workflows.sql
rg --quiet '/radar-scans' contracts/openapi.yaml
rg --quiet '/analysis-cycles' contracts/openapi.yaml
rg --quiet 'simulate-workflows' services/control/cmd/orchestrator/main.go
rg --quiet 'RAINPULSE_ADMIN_TOKEN is required' scripts/control_plane_smoke_test.sh
rg --quiet 'Authorization: Bearer %s' scripts/control_plane_smoke_test.sh
rg --quiet 'curl .*--header @-' scripts/control_plane_smoke_test.sh

printf 'RP-004 three-level control-plane structure checks passed\n'

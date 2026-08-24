#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  deploy/postgres/migrations/0002_control_plane.sql
  deploy/postgres/migrations/0003_completion_idempotency.sql
  services/control/cmd/orchestrator/main.go
  services/control/internal/workflow/state.go
  services/control/internal/orchestration/service.go
  services/control/internal/messaging/jetstream.go
  services/control/internal/postgres/store.go
  scripts/control_plane_smoke_test.sh
)

for path in "${required_files[@]}"; do
  test -f "$path" || { printf 'missing RP-003 file: %s\n' "$path" >&2; exit 1; }
done

rg --quiet '^  orchestrator:' deploy/docker-compose.yaml
rg --quiet 'rainpulse.jobs.requested' contracts/events/README.md
rg --quiet 'rainpulse.jobs.completed' contracts/events/README.md
rg --quiet 'inbox_events' deploy/postgres/migrations/0002_control_plane.sql
rg --quiet 'UNIQUE INDEX.*inbox_events_job_event_type|inbox_events_job_event_type_uidx' deploy/postgres/migrations/0003_completion_idempotency.sql
rg --quiet 'service_healthy' deploy/docker-compose.yaml
rg --quiet 'rainpulse-orchestrator' Makefile

printf 'RP-003 control-plane structure checks passed\n'

#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  deploy/docker-compose.yaml
  deploy/postgres/migrations/0001_initial_schema.sql
  deploy/postgres/migrate.sh
  deploy/nats/nats-server.conf
  deploy/nats/Dockerfile
  deploy/minio/Dockerfile
  deploy/minio/mc.Dockerfile
  scripts/infrastructure_smoke_test.sh
  services/control/cmd/healthcheck/main.go
)

for path in "${required_files[@]}"; do
  test -f "$path" || { printf 'missing RP-002 file: %s\n' "$path" >&2; exit 1; }
done

required_services=(postgres migrate nats minio minio-init api web)
for service in "${required_services[@]}"; do
  rg --quiet "^  ${service}:" deploy/docker-compose.yaml || {
    printf 'Compose service is missing: %s\n' "$service" >&2
    exit 1
  }
done

required_tables=(
  data_sources input_assets forecast_runs jobs job_attempts model_versions
  config_versions model_runs products product_assets verification_runs
  verification_metrics alerts outbox_events
)

for table in "${required_tables[@]}"; do
  rg --ignore-case --quiet "create table( if not exists)? ${table}" deploy/postgres/migrations/0001_initial_schema.sql || {
    printf 'initial migration is missing table: %s\n' "$table" >&2
    exit 1
  }
done

rg --quiet 'UNIQUE.*sha256|sha256.*UNIQUE' deploy/postgres/migrations/0001_initial_schema.sql
rg --quiet 'jetstream' deploy/nats/nats-server.conf
rg --quiet 'healthcheck:' deploy/docker-compose.yaml
rg --quiet 'service_completed_successfully' deploy/docker-compose.yaml
rg --quiet 'postgres-data:|nats-data:|minio-data:' deploy/docker-compose.yaml

printf 'RP-002 infrastructure structure checks passed\n'

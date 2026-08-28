#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

compose_file=${RAINPULSE_COMPOSE_FILE:-deploy/docker-compose.yaml}
env_file=${RAINPULSE_COMPOSE_ENV_FILE:-deploy/.env}
timeout_seconds=${RAINPULSE_SMOKE_TIMEOUT:-90}

if [[ ! -f "$env_file" ]]; then
  printf 'Compose environment file not found: %s\n' "$env_file" >&2
  exit 1
fi

compose=(docker compose --env-file "$env_file" -f "$compose_file")

wait_for_url() {
  local url=$1
  local deadline=$((SECONDS + timeout_seconds))

  until curl --fail --silent --show-error "$url" >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      printf 'timed out waiting for %s\n' "$url" >&2
      return 1
    fi
    sleep 1
  done
}

nats_port=$("${compose[@]}" port nats 8222 | tail -n 1 | awk -F: '{print $NF}')
minio_port=$("${compose[@]}" port minio 9000 | tail -n 1 | awk -F: '{print $NF}')

wait_for_url "http://127.0.0.1:${nats_port}/healthz?js-enabled-only=true"
wait_for_url "http://127.0.0.1:${minio_port}/minio/health/live"

nats_health=$(curl --fail --silent --show-error \
  "http://127.0.0.1:${nats_port}/healthz?js-enabled-only=true")
python3 -c '
import json
import sys

health = json.load(sys.stdin)
if health.get("status") != "ok":
    raise SystemExit(f"NATS JetStream is not healthy: {health!r}")
' <<<"$nats_health"

expected_tables=(
  data_sources input_assets forecast_runs jobs job_attempts model_versions
  config_versions model_runs products product_assets verification_runs
  verification_metrics alerts outbox_events
)

tables=$("${compose[@]}" exec -T postgres \
  psql -X --tuples-only --no-align --username rainpulse --dbname rainpulse \
  --command "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public' ORDER BY tablename")

for table in "${expected_tables[@]}"; do
  grep --fixed-strings --line-regexp --quiet "$table" <<<"$tables" || {
    printf 'database table was not migrated: %s\n' "$table" >&2
    exit 1
  }
done

migration_count=$("${compose[@]}" exec -T postgres \
  psql -X --tuples-only --no-align --username rainpulse --dbname rainpulse \
  --command "SELECT count(*) FROM schema_migrations")
if [[ "$migration_count" -lt 1 ]]; then
  printf 'no database migrations were recorded\n' >&2
  exit 1
fi

bucket=rainpulse
"${compose[@]}" run --rm --no-deps minio-init \
  ls "rainpulse/${bucket}" >/dev/null

printf 'RP-003 infrastructure smoke test passed: PostgreSQL, NATS JetStream, MinIO\n'

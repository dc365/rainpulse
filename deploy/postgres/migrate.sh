#!/usr/bin/env sh

set -eu

migrations_dir=${RAINPULSE_MIGRATIONS_DIR:-/migrations/sql}

psql -X --set ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
SQL

for migration in "$migrations_dir"/*.sql; do
  version=$(basename "$migration")
  escaped_version=$(printf '%s' "$version" | sed "s/'/''/g")
  applied=$(psql -X --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --command "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = '$escaped_version')")

  if [ "$applied" = "t" ]; then
    printf 'migration already applied: %s\n' "$version"
    continue
  fi

  {
    printf 'SET TIME ZONE '\''UTC'\'';\n'
    sed '/^[[:space:]]*$/d' "$migration"
    printf "INSERT INTO schema_migrations (version) VALUES ('%s');\n" "$escaped_version"
  } | psql -X --single-transaction --set ON_ERROR_STOP=1
  printf 'migration applied: %s\n' "$version"
done

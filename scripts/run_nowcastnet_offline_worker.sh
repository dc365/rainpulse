#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd "$script_dir/.." && pwd)
compose_env=${RAINPULSE_COMPOSE_ENV_FILE:-$repository_root/deploy/.env}

if [[ ! -f "$compose_env" ]]; then
  echo "NowcastNet worker environment is missing: $compose_env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090 -- deployment selects the untracked Compose environment file.
source "$compose_env"
set +a

required=(
  RAINPULSE_NATS_USER
  RAINPULSE_NATS_PASSWORD
  RAINPULSE_MINIO_WORKER_ACCESS_KEY
  RAINPULSE_MINIO_WORKER_SECRET_KEY
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "required NowcastNet worker setting is missing: $name" >&2
    exit 1
  fi
done

if [[ ! "$RAINPULSE_NATS_USER" =~ ^[A-Za-z0-9]+$ ]] ||
  [[ ! "$RAINPULSE_NATS_PASSWORD" =~ ^[A-Za-z0-9]+$ ]]; then
  echo "NATS worker credentials must be URL-safe alphanumeric values" >&2
  exit 1
fi

export RAINPULSE_NATS_URL="nats://${RAINPULSE_NATS_USER}:${RAINPULSE_NATS_PASSWORD}@127.0.0.1:${RAINPULSE_NATS_PORT:-4222}"
export RAINPULSE_OBJECT_STORE_ENDPOINT="http://127.0.0.1:${RAINPULSE_MINIO_PORT:-9000}"
export RAINPULSE_OBJECT_STORE_ACCESS_KEY="$RAINPULSE_MINIO_WORKER_ACCESS_KEY"
export RAINPULSE_OBJECT_STORE_SECRET_KEY="$RAINPULSE_MINIO_WORKER_SECRET_KEY"
export RAINPULSE_MAX_INPUT_ARTIFACT_BYTES="${RAINPULSE_MAX_INPUT_ARTIFACT_BYTES:-2147483648}"

exec "$repository_root/runtime/nowcastnet/venv/bin/python" -m rainpulse_algo.worker

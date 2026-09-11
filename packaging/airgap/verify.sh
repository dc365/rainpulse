#!/usr/bin/env bash

set -euo pipefail

package_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
environment_file="$package_root/deploy/.env"
mode=base

usage() {
  cat <<'EOF'
Usage: ./verify.sh [--env-file PATH] [--mode base|realtime-shadow|unified]

Verify Compose configuration, container health, and the local API/Web entry
points after an air-gap deployment. It performs no data ingestion or deletion.
EOF
}

while (($#)); do
  case "$1" in
    --env-file)
      environment_file=${2:?missing path after --env-file}
      shift 2
      ;;
    --mode)
      mode=${2:?missing mode after --mode}
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$mode" == base || "$mode" == realtime-shadow || "$mode" == unified ]] || {
  printf 'unsupported deployment mode: %s\n' "$mode" >&2
  exit 2
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'required command is unavailable: %s\n' "$1" >&2
    exit 1
  }
}

for command in awk curl docker grep; do
  require_command "$command"
done
[[ -f "$environment_file" ]] || {
  printf 'Compose environment file is unavailable: %s\n' "$environment_file" >&2
  exit 1
}

docker_command=(docker)
if ! docker info >/dev/null 2>&1; then
  require_command sudo
  sudo -v
  docker_command=(sudo docker)
fi
compose_command=(
  "${docker_command[@]}" compose --env-file "$environment_file"
  -f "$package_root/deploy/docker-compose.yaml"
)
if [[ "$mode" == realtime-shadow || "$mode" == unified ]]; then
  compose_command+=(-f "$package_root/deploy/docker-compose.realtime-shadow.yaml")
fi

if [[ "$mode" == unified ]]; then
  compose_command+=(-f "$package_root/deploy/docker-compose.unified.yaml")
  systemctl is-active --quiet rainpulse
fi

"${compose_command[@]}" config --quiet
"${compose_command[@]}" ps

api_port="$(awk -F= '$1 == "RAINPULSE_API_PORT" {print $2}' "$environment_file" | tail -n 1)"
web_port="$(awk -F= '$1 == "RAINPULSE_WEB_PORT" {print $2}' "$environment_file" | tail -n 1)"
api_port=${api_port:-8080}
web_port=${web_port:-4173}
[[ "$api_port" =~ ^[0-9]+$ && "$web_port" =~ ^[0-9]+$ ]] || {
  printf 'API/Web port values must be numeric\n' >&2
  exit 1
}

api_response="$(curl --fail --silent --show-error "http://127.0.0.1:$api_port/api/v1/system/status")"
grep -Eq '"service"[[:space:]]*:[[:space:]]*"rainpulse-control"' <<<"$api_response" || {
  printf 'RainPulse API did not return the expected service identity\n' >&2
  exit 1
}
web_response="$(curl --fail --silent --show-error "http://127.0.0.1:$web_port/")"
grep -Fq '<title>RainPulse</title>' <<<"$web_response" || {
  printf 'RainPulse Web title was not found\n' >&2
  exit 1
}

printf 'RainPulse air-gap verification passed: mode=%s api=%s web=%s\n' \
  "$mode" "$api_port" "$web_port"

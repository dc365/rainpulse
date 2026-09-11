#!/usr/bin/env bash

set -euo pipefail

package_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
environment_file="$package_root/deploy/.env"
mode=base
service_user="${SUDO_USER:-$(id -un)}"
replace_existing=false

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Load the supplied RainPulse air-gap images and start either the base service
stack or the realtime-shadow stack. Create deploy/.env from deploy/.env.example
and set environment-specific secrets before running this command.

Options:
  --env-file PATH            Compose environment file (default: deploy/.env)
  --mode base|realtime-shadow|unified
  --service-user NAME        Host service account for unified mode (default: caller)
                              Base is the default and keeps ingest/pipeline disabled.
  --replace-existing         Permit image load and Compose replacement on an existing host.
  -h, --help                 Show this help
EOF
}

while (($#)); do
  case "$1" in
    --service-user)
      service_user=${2:?missing service user}
      shift 2
      ;;
    --env-file)
      environment_file=${2:?missing path after --env-file}
      shift 2
      ;;
    --mode)
      mode=${2:?missing mode after --mode}
      shift 2
      ;;
    --replace-existing)
      replace_existing=true
      shift
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

for command in sha256sum awk docker; do
  require_command "$command"
done
[[ -f "$package_root/SHA256SUMS" && -f "$package_root/images/rainpulse-images.tar" ]] || {
  printf 'this directory is not a complete RainPulse air-gap package\n' >&2
  exit 1
}
[[ -f "$environment_file" ]] || {
  printf 'create %s from deploy/.env.example and set the required secrets first\n' "$environment_file" >&2
  exit 1
}

if [[ -f "$package_root/.build/linux-amd64/rainpulse" && "$mode" != unified ]]; then
  echo 'This is a unified package; use --mode unified.' >&2
  exit 2
fi
environment_file="$(cd "$(dirname "$environment_file")" && pwd)/$(basename "$environment_file")"

required_secrets=(
  RAINPULSE_POSTGRES_PASSWORD
  RAINPULSE_MINIO_ROOT_USER
  RAINPULSE_MINIO_ROOT_PASSWORD
  RAINPULSE_MINIO_WORKER_ACCESS_KEY
  RAINPULSE_MINIO_WORKER_SECRET_KEY
  RAINPULSE_NATS_USER
  RAINPULSE_NATS_PASSWORD
)
for name in "${required_secrets[@]}"; do
  value="$(awk -F= -v name="$name" '$1 == name {print substr($0, length(name) + 2)}' "$environment_file" | tail -n 1)"
  [[ -n "$value" ]] || {
    printf 'required secret is empty in %s: %s\n' "$environment_file" "$name" >&2
    exit 1
  }
done

docker_command=(docker)
if ! docker info >/dev/null 2>&1; then
  require_command sudo
  sudo -v
  docker_command=(sudo docker)
fi
"${docker_command[@]}" compose version >/dev/null

if [[ "$replace_existing" == false ]]; then
  while IFS= read -r image; do
    [[ -n "$image" ]] || continue
    if [[ "$image" == rainpulse-* || "$image" == rainpulse/* ]] \
      && "${docker_command[@]}" image inspect "$image" >/dev/null 2>&1; then
      printf 'existing RainPulse image found: %s (rerun with --replace-existing to update)\n' "$image" >&2
      exit 1
    fi
  done <"$package_root/images/images.txt"
fi

(
  cd "$package_root"
  sha256sum -c SHA256SUMS
)
"${docker_command[@]}" load --input "$package_root/images/rainpulse-images.tar"

while IFS= read -r image; do
  [[ -n "$image" ]] || continue
  "${docker_command[@]}" image inspect "$image" >/dev/null || {
    printf 'loaded image is unavailable: %s\n' "$image" >&2
    exit 1
  }
done <"$package_root/images/images.txt"

compose_command=(
  "${docker_command[@]}" compose --env-file "$environment_file"
  -f "$package_root/deploy/docker-compose.yaml"
)
if [[ "$mode" == realtime-shadow || "$mode" == unified ]]; then
  bdp_root="$(awk -F= '$1 == "RAINPULSE_BDP_CONF_HOST_ROOT" {print substr($0, length($1) + 2)}' "$environment_file" | tail -n 1)"
  bdp_root=${bdp_root:-../runtime/bdp-conf}
  [[ "$bdp_root" == /* ]] || bdp_root="$package_root/deploy/$bdp_root"
  [[ -d "$bdp_root" ]] || {
    printf 'realtime-shadow requires an existing RAINPULSE_BDP_CONF_HOST_ROOT directory\n' >&2
    exit 1
  }
  radar_root="$(awk -F= '$1 == "RAINPULSE_RADAR_DATA_ROOT" {print substr($0, length($1) + 2)}' "$environment_file" | tail -n 1)"
  radar_root=${radar_root:-/data/Weather/RADA/RADA_L2_FMT/OBS_TEMP}
  [[ "$radar_root" == /* && -d "$radar_root" ]] || {
    printf 'realtime-shadow requires an existing absolute RAINPULSE_RADAR_DATA_ROOT directory\n' >&2
    exit 1
  }
  compose_command+=(-f "$package_root/deploy/docker-compose.realtime-shadow.yaml")
fi

mkdir -p "$package_root/runtime/reports/mrms" \
  "$package_root/runtime/reports/workspace-verification" \
  "$package_root/runtime/products/ensemble" \
  "$package_root/runtime/products/nowcastnet"
"${compose_command[@]}" config --quiet
if [[ "$mode" == unified ]]; then
  require_command python3
  [[ -f "$package_root/.build/linux-amd64/rainpulse" ]] || { echo 'Not a unified package.' >&2; exit 1; }
  compose_command+=(-f "$package_root/deploy/docker-compose.unified.yaml")
  "${compose_command[@]}" up -d --no-build --pull never --wait migrate nats minio-worker-policy
  privileged=()
  if [[ $EUID -ne 0 ]]; then privileged=(sudo); fi
  "${privileged[@]}" python3 "$package_root/scripts/configure_unified.py" --root "$package_root" --user "$service_user" --env-file "$environment_file"
  "${privileged[@]}" env RAINPULSE_DEPLOY_ENV_FILE="$environment_file" bash "$package_root/scripts/switch_unified.sh"
  "$package_root/verify.sh" --env-file "$environment_file" --mode unified
  exit
fi
"${compose_command[@]}" up -d --no-build --pull never --wait
"$package_root/verify.sh" --env-file "$environment_file" --mode "$mode"

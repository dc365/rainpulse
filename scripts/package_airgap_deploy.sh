#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
environment_file=""
output_path=""
include_realtime_shadow=true
installer_root="$repository_root/packaging/airgap"

usage() {
  cat <<'EOF'
Usage: scripts/package_airgap_deploy.sh [options]

Create a self-contained RainPulse air-gap deployment ZIP from images already
available in the local Docker image store. The package deliberately excludes
deploy/.env, BDP credentials/configuration, radar/static data, model capsules,
and all generated runtime data.

Options:
  --env-file PATH            Existing Compose environment file (default: deploy/.env)
  --source-root PATH         RainPulse source root to archive (default: script parent)
  --output PATH              Destination ZIP (default: .build/rainpulse-airgap-<git-sha>.zip)
  --base-only                Export only the base Compose stack, not realtime-shadow images
  --installer-root PATH      Directory containing install.sh and verify.sh
                             (default: packaging/airgap in this source tree)
  -h, --help                 Show this help

Run this on the connected build/deployment host with Docker privileges. If the
current user cannot access Docker directly, the script requests sudo; it never
records a sudo password in the package or repository.
EOF
}

while (($#)); do
  case "$1" in
    --env-file)
      environment_file=${2:?missing path after --env-file}
      shift 2
      ;;
    --source-root)
      repository_root=${2:?missing path after --source-root}
      shift 2
      ;;
    --output)
      output_path=${2:?missing path after --output}
      shift 2
      ;;
    --base-only)
      include_realtime_shadow=false
      shift
      ;;
    --installer-root)
      installer_root=${2:?missing path after --installer-root}
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

repository_root="$(cd "$repository_root" && pwd)"
if [[ -z "$environment_file" ]]; then
  environment_file="$repository_root/deploy/.env"
fi

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'required command is unavailable: %s\n' "$1" >&2
    exit 1
  }
}

require_command git
require_command tar
require_command zip
require_command sha256sum

[[ -f "$environment_file" ]] || {
  printf 'Compose environment file is required: %s\n' "$environment_file" >&2
  exit 1
}
[[ -f "$installer_root/install.sh" && -f "$installer_root/verify.sh" ]] || {
  printf 'air-gap installer files are required under: %s\n' "$installer_root" >&2
  exit 1
}

docker_command=(docker)
if ! docker info >/dev/null 2>&1; then
  require_command sudo
  sudo -v
  docker_command=(sudo docker)
fi
"${docker_command[@]}" compose version >/dev/null

revision="$(git -C "$repository_root" rev-parse HEAD)"
short_revision="${revision:0:12}"
if [[ -z "$output_path" ]]; then
  output_path="$repository_root/.build/rainpulse-airgap-$short_revision.zip"
fi
mkdir -p "$(dirname "$output_path")"
output_path="$(cd "$(dirname "$output_path")" && pwd)/$(basename "$output_path")"
[[ ! -e "$output_path" ]] || {
  printf 'refusing to overwrite existing package: %s\n' "$output_path" >&2
  exit 1
}
[[ ! -e "$output_path.sha256" ]] || {
  printf 'refusing to overwrite existing checksum: %s.sha256\n' "$output_path" >&2
  exit 1
}

compose_command=(
  "${docker_command[@]}" compose --env-file "$environment_file"
  -f "$repository_root/deploy/docker-compose.yaml"
)
if [[ "$include_realtime_shadow" == true ]]; then
  compose_command+=(
    -f "$repository_root/deploy/docker-compose.realtime-shadow.yaml"
  )
fi

mapfile -t images < <("${compose_command[@]}" config --images | LC_ALL=C sort -u)
((${#images[@]} > 0)) || {
  printf 'Compose did not resolve any images.\n' >&2
  exit 1
}
for image in "${images[@]}"; do
  "${docker_command[@]}" image inspect "$image" >/dev/null 2>&1 || {
    printf 'required image is not available locally: %s\n' "$image" >&2
    exit 1
  }
done

staging_root="$(mktemp -d "${TMPDIR:-/tmp}/rainpulse-airgap.XXXXXX")"
cleanup() {
  rm -rf "$staging_root"
}
trap cleanup EXIT

package_name="rainpulse-airgap-$short_revision"
package_root="$staging_root/$package_name"
mkdir -p "$package_root/images"

# Archive only tracked deployment/configuration files from the selected commit.
# This prevents local radar experiments, deploy/.env, and generated outputs from
# entering an air-gap package accidentally.
git -C "$repository_root" archive --format=tar "$revision" deploy configs \
  | tar -x -C "$package_root"
install -m 0755 "$installer_root/install.sh" "$package_root/install.sh"
install -m 0755 "$installer_root/verify.sh" "$package_root/verify.sh"

printf '%s\n' "${images[@]}" >"$package_root/images/images.txt"
image_archive="$package_root/images/rainpulse-images.tar"
"${docker_command[@]}" image save --output "$image_archive" "${images[@]}"
# Docker writes an archive as root when sudo is required. Hand it back to the
# invoking user before computing the package checksum and ZIP.
if [[ "${docker_command[0]}" == sudo ]]; then
  sudo chown "$(id -u):$(id -g)" "$image_archive"
fi

mode=base_and_realtime_shadow
if [[ "$include_realtime_shadow" == false ]]; then
  mode=base_only
fi
{
  printf '{\n'
  printf '  "package_format": "rainpulse-airgap/1.0",\n'
  printf '  "source_revision": "%s",\n' "$revision"
  printf '  "image_set": "%s",\n' "$mode"
  printf '  "image_count": %d,\n' "${#images[@]}"
  printf '  "excluded": ["deploy/.env", "BDP configuration and credentials", "radar/static/model data", "runtime volumes and generated products"]\n'
  printf '}\n'
} >"$package_root/manifest.json"

(
  cd "$package_root"
  find . -type f ! -name SHA256SUMS -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum >SHA256SUMS
)
(
  cd "$staging_root"
  zip -q -r "$output_path" "$package_name"
)
sha256sum "$output_path" >"$output_path.sha256"

printf 'Created offline package: %s\n' "$output_path"
printf 'Created package checksum: %s.sha256\n' "$output_path"
printf 'Source revision: %s\n' "$revision"
printf 'Images exported: %d (%s)\n' "${#images[@]}" "$mode"

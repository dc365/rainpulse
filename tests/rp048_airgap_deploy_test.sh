#!/usr/bin/env bash

set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  scripts/package_airgap_deploy.sh
  packaging/airgap/install.sh
  packaging/airgap/verify.sh
  docs/内网离线部署.md
)
for relative_path in "${required_files[@]}"; do
  [[ -s "$repository_root/$relative_path" ]] || {
    printf 'missing air-gap deployment artifact: %s\n' "$relative_path" >&2
    exit 1
  }
done

bash "$repository_root/scripts/package_airgap_deploy.sh" --help >/dev/null
bash "$repository_root/packaging/airgap/install.sh" --help >/dev/null
bash "$repository_root/packaging/airgap/verify.sh" --help >/dev/null

rg --quiet 'git -C "\$repository_root" archive' \
  "$repository_root/scripts/package_airgap_deploy.sh"
rg --quiet 'deploy/\.env' "$repository_root/scripts/package_airgap_deploy.sh"
rg --quiet -- '--no-build --pull never --wait' \
  "$repository_root/packaging/airgap/install.sh"
rg --quiet 'realtime-shadow requires' "$repository_root/packaging/airgap/install.sh"
rg --quiet 'docker load' "$repository_root/docs/内网离线部署.md"

printf 'RP-048 air-gap deployment package artifacts are present.\n'

#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="${1:-$repository_root/.build/bdp-go}"

find_bdp_root() {
  if [[ -n "${RUIYUN_BDP_ROOT:-}" ]]; then
    printf '%s\n' "$RUIYUN_BDP_ROOT"
    return
  fi

  local cursor="$repository_root"
  while [[ "$cursor" != "/" ]]; do
    if [[ -d "$cursor/bdp-publiccode/bdp-publiccode-go/bdp-publiccode-common" ]]; then
      printf '%s\n' "$cursor"
      return
    fi
    if [[ -d "$cursor/ruiyun-bdp/bdp-publiccode/bdp-publiccode-go/bdp-publiccode-common" ]]; then
      printf '%s\n' "$cursor/ruiyun-bdp"
      return
    fi
    cursor="$(dirname "$cursor")"
  done
  return 1
}

bdp_root="$(find_bdp_root || true)"
if [[ -z "$bdp_root" ]]; then
  echo "Ruiyun BDP source tree not found; set RUIYUN_BDP_ROOT" >&2
  exit 10
fi

go_root="${RUIYUN_GO_ROOT:-$(dirname "$bdp_root")/go}"
common_root="$bdp_root/bdp-publiccode/bdp-publiccode-go/bdp-publiccode-common"
puremanage_root="$bdp_root/bdp-publiccode/bdp-publiccode-go/bdp-publiccode-puremanage"
hw_common_root="$go_root/hw-common"

for required in "$common_root/go.mod" "$puremanage_root/go.mod" "$hw_common_root/go.mod"; do
  if [[ ! -f "$required" ]]; then
    echo "required Ruiyun BDP Go module is missing: $required" >&2
    exit 10
  fi
done

mkdir -p "$workspace_dir"
workspace_dir="$(cd "$workspace_dir" && pwd)"
mod_file="$workspace_dir/rainpulse.mod"
sum_file="$workspace_dir/rainpulse.sum"
staging_dir="$(mktemp -d "$workspace_dir/prepare.XXXXXX")"
staging_mod="$staging_dir/rainpulse.mod"
staging_sum="$staging_dir/rainpulse.sum"
cp "$repository_root/services/control/go.mod" "$staging_mod"
if [[ -f "$repository_root/services/control/go.sum" ]]; then
  cp "$repository_root/services/control/go.sum" "$staging_sum"
fi
GOWORK=off go -C "$repository_root/services/control" mod edit -modfile="$staging_mod" \
  -require="bdp-publiccode-common@v1.0.0" \
  -require="bdp-publiccode-puremanage@v1.0.0" \
  -replace="bdp-publiccode-common@v1.0.0=$common_root" \
  -replace="bdp-publiccode-puremanage@v1.0.0=$puremanage_root" \
  -replace="hw-common@v1.0.0=$hw_common_root"
GOWORK=off go -C "$repository_root/services/control" mod tidy -modfile="$staging_mod"
mv "$staging_mod" "$mod_file"
if [[ -f "$staging_sum" ]]; then
  mv "$staging_sum" "$sum_file"
fi
rmdir "$staging_dir"

echo "$mod_file"

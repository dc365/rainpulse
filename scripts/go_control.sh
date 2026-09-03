#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
control_root="$repository_root/services/control"

set +e
mod_file="$(bash "$repository_root/scripts/prepare_bdp_go_workspace.sh" "$repository_root/.build/bdp-go" 2>/dev/null)"
prepare_status=$?
set -e
if [[ $prepare_status -eq 0 ]]; then
  if [[ "${1:-}" == "--prepare" ]]; then
    exit 0
  fi
  if [[ $# -eq 0 ]]; then
    echo "Go command is required" >&2
    exit 2
  fi
  command="$1"
  shift
  exec env GOWORK=off go -C "$control_root" "$command" \
    -modfile="$mod_file" -tags=ruiyun_bdp "$@"
fi

if [[ $prepare_status -ne 10 || "${RAINPULSE_REQUIRE_BDP_SOURCE:-0}" == "1" ]]; then
  bash "$repository_root/scripts/prepare_bdp_go_workspace.sh" "$repository_root/.build/bdp-go" >/dev/null
  exit 1
fi
if [[ "${1:-}" == "--prepare" ]]; then
  exit 0
fi
if [[ $# -eq 0 ]]; then
  echo "Go command is required" >&2
  exit 2
fi
exec env GOWORK=off go -C "$control_root" "$@"

#!/usr/bin/env bash

set -euo pipefail

api_url=${RAINPULSE_API_URL:-http://127.0.0.1:8080}
web_url=${RAINPULSE_WEB_URL:-http://127.0.0.1:4173}
timeout_seconds=${RAINPULSE_SMOKE_TIMEOUT:-60}

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

wait_for_url "$api_url/api/v1/system/status"
wait_for_url "$web_url/"

api_response=$(curl --fail --silent --show-error "$api_url/api/v1/system/status")
python3 -c '
import json
import sys

status = json.load(sys.stdin)
expected = {"service": "rainpulse-control", "status": "ready"}
for key, value in expected.items():
    if status.get(key) != value:
        raise SystemExit(f"unexpected {key}: {status.get(key)!r}")
if not status.get("version"):
    raise SystemExit("status response has no version")
' <<<"$api_response"

web_response=$(curl --fail --silent --show-error "$web_url/")
if [[ "$web_response" != *"<title>RainPulse</title>"* ]]; then
  printf 'RainPulse web title was not found\n' >&2
  exit 1
fi

printf 'RainPulse smoke test passed: api=%s web=%s\n' "$api_url" "$web_url"

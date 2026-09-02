#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repository_root"

mkdir -p .build/linux-amd64
# The shadow probe reuses the standard long-lived Python worker image. Build
# its pinned site-packages before Docker evaluates algorithms/worker.Dockerfile.
make build-worker-linux

revision="$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
version="${RAINPULSE_VERSION:-$revision}"
ldflags="-s -w -X github.com/fonwee/rainpulse-nowcast/services/control/internal/buildinfo.Version=$version -X github.com/fonwee/rainpulse-nowcast/services/control/internal/buildinfo.Revision=$revision"
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath \
  -ldflags="$ldflags" -o .build/linux-amd64/rainpulse-api ./services/control/cmd/api
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath \
  -ldflags="$ldflags" -o .build/linux-amd64/rainpulse-orchestrator ./services/control/cmd/orchestrator
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath \
  -ldflags="$ldflags" -o .build/linux-amd64/rainpulse-ingest ./services/control/cmd/ingest
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath \
  -ldflags="-s -w" -o .build/linux-amd64/rainpulse-healthcheck ./services/control/cmd/healthcheck
pnpm --filter @rainpulse/web build

echo "Built RainPulse realtime-shadow control binaries and Web assets."

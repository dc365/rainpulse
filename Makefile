SHELL := /usr/bin/env bash

COMPOSE_FILE := deploy/docker-compose.yaml
COMPOSE_ENV_FILE ?= deploy/.env
POSTGRES_IMAGE := postgres:17.11-alpine3.24
PYTHON_IMAGE := python:3.13.12-slim-bookworm
CRANE_VERSION := v0.21.9
NATS_VERSION := v2.14.5
MINIO_VERSION := RELEASE.2025-10-15T17-29-55Z
MINIO_MC_VERSION := RELEASE.2025-08-13T08-35-41Z
MINIO_BUILD_VERSION := 2025-10-15T17:29:55Z
MINIO_COMMIT := 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
MINIO_LDFLAGS := -s -w -X github.com/minio/minio/cmd.Version=$(MINIO_BUILD_VERSION) -X github.com/minio/minio/cmd.CopyrightYear=2025 -X github.com/minio/minio/cmd.ReleaseTag=$(MINIO_VERSION) -X github.com/minio/minio/cmd.CommitID=$(MINIO_COMMIT) -X github.com/minio/minio/cmd.ShortCommitID=9e49d5e7a648
MINIO_MC_BUILD_VERSION := 2025-08-13T08:35:41Z
MINIO_MC_COMMIT := 7394ce0dd2a80935aded936b09fa12cbb3cb8096
MINIO_MC_LDFLAGS := -s -w -X github.com/minio/mc/cmd.Version=$(MINIO_MC_BUILD_VERSION) -X github.com/minio/mc/cmd.CopyrightYear=2025 -X github.com/minio/mc/cmd.ReleaseTag=$(MINIO_MC_VERSION) -X github.com/minio/mc/cmd.CommitID=$(MINIO_MC_COMMIT) -X github.com/minio/mc/cmd.ShortCommitID=7394ce0dd2a8

.PHONY: bootstrap contracts-generate contracts-check test test-structure test-radar-config test-contracts test-infrastructure test-control-plane test-worker-sdk test-radar-decoder test-radar-health test-radar-qc test-radar-grid test-radar-mosaic test-ancillary test-grid test-go test-python test-web lint build build-linux build-infrastructure-linux export-postgres-image export-python-image build-worker-linux deploy-up dev-up dev-down smoke infrastructure-smoke control-plane-smoke worker-smoke radar-decode-smoke radar-health-smoke radar-qc-smoke radar-grid-smoke ancillary-plan ancillary-download ancillary-verify

bootstrap:
	@command -v go >/dev/null || { echo "go is required" >&2; exit 1; }
	@command -v pnpm >/dev/null || { echo "pnpm is required" >&2; exit 1; }
	@command -v uv >/dev/null || { echo "uv is required" >&2; exit 1; }
	pnpm install --frozen-lockfile
	uv sync --project algorithms --dev
	go list -C services/control -buildvcs=false -deps ./... >/dev/null

contracts-generate:
	bash scripts/generate_contracts.sh

contracts-check:
	bash scripts/check_generated_contracts.sh

test: test-structure test-radar-config test-contracts test-infrastructure test-control-plane test-worker-sdk test-radar-decoder test-radar-health test-radar-qc test-radar-grid test-radar-mosaic test-ancillary test-grid test-go test-python test-web

test-structure:
	bash tests/rp000_structure_test.sh

test-radar-config:
	bash tests/rp001_radar_config_test.sh
	uv run --project algorithms pytest configs/tests

test-contracts:
	bash tests/rp002_contracts_test.sh
	uv run --project algorithms pytest contracts/tests

test-infrastructure:
	bash tests/rp003_infrastructure_test.sh

test-control-plane:
	bash tests/rp004_control_plane_test.sh

test-worker-sdk:
	bash tests/rp005_worker_sdk_test.sh

test-radar-decoder:
	bash tests/rp006_radar_decoder_test.sh

test-radar-health:
	bash tests/rp007_radar_health_test.sh

test-radar-qc:
	bash tests/rp008_radar_qc_test.sh

test-radar-grid:
	bash tests/rp009_radar_grid_test.sh
	uv run --project algorithms pytest algorithms/tests/test_radar_grid.py

test-radar-mosaic:
	bash tests/rp010_radar_mosaic_test.sh
	uv run --project algorithms pytest algorithms/tests/test_radar_mosaic.py

test-ancillary:
	uv run --project algorithms pytest algorithms/tests/test_ancillary.py

test-grid:
	uv run --project algorithms pytest algorithms/tests/test_grid.py

test-go:
	go test ./services/control/...

test-python:
	uv run --project algorithms pytest algorithms/tests

test-web:
	pnpm --filter @rainpulse/web test

lint:
	@test -z "$$(gofmt -l services/control)" || { gofmt -l services/control; exit 1; }
	go vet ./services/control/...
	ruff check algorithms
	ruff check configs/tests
	ruff check contracts/tests
	pnpm --filter @rainpulse/web lint
	$(MAKE) contracts-check

build:
	mkdir -p .build/python
	go build -buildvcs=false -trimpath -o .build/rainpulse-api ./services/control/cmd/api
	go build -buildvcs=false -trimpath -o .build/rainpulse-web ./services/control/cmd/web
	go build -buildvcs=false -trimpath -o .build/rainpulse-orchestrator ./services/control/cmd/orchestrator
	uv build --project algorithms --out-dir .build/python
	pnpm --filter @rainpulse/web build

build-infrastructure-linux:
	mkdir -p .build/linux-amd64
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOBIN= go install -trimpath -ldflags="-s -w" github.com/nats-io/nats-server/v2@$(NATS_VERSION)
	cp "$$(go env GOPATH)/bin/linux_amd64/nats-server" .build/linux-amd64/nats-server
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOBIN= go install -trimpath -ldflags="$(MINIO_LDFLAGS)" github.com/minio/minio@$(MINIO_VERSION)
	cp "$$(go env GOPATH)/bin/linux_amd64/minio" .build/linux-amd64/minio
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOBIN= go install -trimpath -ldflags="$(MINIO_MC_LDFLAGS)" github.com/minio/mc@$(MINIO_MC_VERSION)
	cp "$$(go env GOPATH)/bin/linux_amd64/mc" .build/linux-amd64/mc
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath -ldflags="-s -w" -o .build/linux-amd64/rainpulse-healthcheck ./services/control/cmd/healthcheck

build-linux: build-infrastructure-linux
	mkdir -p .build/linux-amd64
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath -ldflags="-s -w" -o .build/linux-amd64/rainpulse-api ./services/control/cmd/api
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath -ldflags="-s -w" -o .build/linux-amd64/rainpulse-web ./services/control/cmd/web
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath -ldflags="-s -w" -o .build/linux-amd64/rainpulse-orchestrator ./services/control/cmd/orchestrator
	pnpm --filter @rainpulse/web build

export-postgres-image:
	mkdir -p .build/tools
	GOBIN="$(CURDIR)/.build/tools" go install github.com/google/go-containerregistry/cmd/crane@$(CRANE_VERSION)
	.build/tools/crane pull --platform linux/amd64 $(POSTGRES_IMAGE) .build/postgres-17.11-alpine3.24-linux-amd64.tar

export-python-image:
	mkdir -p .build/tools
	GOBIN="$(CURDIR)/.build/tools" go install github.com/google/go-containerregistry/cmd/crane@$(CRANE_VERSION)
	.build/tools/crane pull --platform linux/amd64 $(PYTHON_IMAGE) .build/python-3.13.12-slim-bookworm-linux-amd64.tar

build-worker-linux:
	mkdir -p .build
	uv export --project algorithms --locked --no-dev --no-emit-project --format requirements.txt --output-file .build/worker-requirements.txt
	rm -rf .build/worker-site-packages
	uv pip install --target .build/worker-site-packages --requirements .build/worker-requirements.txt --python-version 3.13.12 --python-platform x86_64-manylinux_2_28 --only-binary :all: --no-binary asciitree

deploy-up:
	@test -f "$(COMPOSE_ENV_FILE)" || { echo "create $(COMPOSE_ENV_FILE) from deploy/.env.example and set required secrets" >&2; exit 1; }
	docker compose --env-file $(COMPOSE_ENV_FILE) -f $(COMPOSE_FILE) up -d --build --wait

dev-up: build-linux build-worker-linux deploy-up

dev-down:
	@test -f "$(COMPOSE_ENV_FILE)" || { echo "missing $(COMPOSE_ENV_FILE)" >&2; exit 1; }
	docker compose --env-file $(COMPOSE_ENV_FILE) -f $(COMPOSE_FILE) down --remove-orphans

smoke:
	bash scripts/smoke_test.sh

infrastructure-smoke:
	bash scripts/infrastructure_smoke_test.sh

control-plane-smoke:
	bash scripts/control_plane_smoke_test.sh

worker-smoke:
	bash scripts/worker_smoke_test.sh

radar-decode-smoke:
	bash scripts/radar_decode_smoke_test.sh

radar-health-smoke:
	bash scripts/radar_health_smoke_test.sh

radar-qc-smoke:
	bash scripts/radar_qc_smoke_test.sh

radar-grid-smoke:
	bash scripts/radar_grid_smoke_test.sh

ANCILLARY_CONFIG ?= configs/ancillary/fujian-taiwan-v1.yaml
ANCILLARY_ROOT ?= runtime/ancillary/assets
ANCILLARY_WORKERS ?= 4
ANCILLARY_PROXY ?=

ancillary-plan:
	uv run --project algorithms python -m rainpulse_algo.radar.ancillary --config $(ANCILLARY_CONFIG) plan

ancillary-download:
	uv run --project algorithms python -m rainpulse_algo.radar.ancillary --config $(ANCILLARY_CONFIG) download --root $(ANCILLARY_ROOT) --workers $(ANCILLARY_WORKERS) $(if $(ANCILLARY_PROXY),--proxy $(ANCILLARY_PROXY),)

ancillary-verify:
	uv run --project algorithms python -m rainpulse_algo.radar.ancillary --config $(ANCILLARY_CONFIG) verify --root $(ANCILLARY_ROOT)

SHELL := /usr/bin/env bash

COMPOSE_FILE := deploy/docker-compose.yaml
COMPOSE_ENV_FILE ?= deploy/.env
POSTGRES_IMAGE := postgres:17.11-alpine3.24
PYTHON_IMAGE := python:3.13.12-slim-bookworm
CRANE_VERSION := v0.21.9
NODE_EXPORTER_VERSION := v1.9.1
NATS_VERSION := v2.14.5
MINIO_VERSION := RELEASE.2025-10-15T17-29-55Z
MINIO_MC_VERSION := RELEASE.2025-08-13T08-35-41Z
MINIO_BUILD_VERSION := 2025-10-15T17:29:55Z
MINIO_COMMIT := 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
MINIO_LDFLAGS := -s -w -X github.com/minio/minio/cmd.Version=$(MINIO_BUILD_VERSION) -X github.com/minio/minio/cmd.CopyrightYear=2025 -X github.com/minio/minio/cmd.ReleaseTag=$(MINIO_VERSION) -X github.com/minio/minio/cmd.CommitID=$(MINIO_COMMIT) -X github.com/minio/minio/cmd.ShortCommitID=9e49d5e7a648
MINIO_MC_BUILD_VERSION := 2025-08-13T08:35:41Z
MINIO_MC_COMMIT := 7394ce0dd2a80935aded936b09fa12cbb3cb8096
MINIO_MC_LDFLAGS := -s -w -X github.com/minio/mc/cmd.Version=$(MINIO_MC_BUILD_VERSION) -X github.com/minio/mc/cmd.CopyrightYear=2025 -X github.com/minio/mc/cmd.ReleaseTag=$(MINIO_MC_VERSION) -X github.com/minio/mc/cmd.CommitID=$(MINIO_MC_COMMIT) -X github.com/minio/mc/cmd.ShortCommitID=7394ce0dd2a8
BUILD_REVISION ?= $(shell git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
BUILD_VERSION ?= $(BUILD_REVISION)
RAINPULSE_GO_LDFLAGS := -X github.com/fonwee/rainpulse-nowcast/services/control/internal/buildinfo.Version=$(BUILD_VERSION) -X github.com/fonwee/rainpulse-nowcast/services/control/internal/buildinfo.Revision=$(BUILD_REVISION)

.PHONY: bootstrap contracts-generate contracts-check test test-structure test-radar-config test-contracts test-infrastructure test-alerting test-operations test-control-plane test-worker-sdk test-radar-decoder test-radar-health test-radar-qc test-radar-grid test-radar-mosaic test-qpe test-diagnostics test-nowcast-input test-pysteps-lk test-pysteps-steps test-probability-calibration test-nowcastnet test-nowcastnet-training test-nowcastnet-pilot test-products test-ensemble-products test-ancillary test-grid test-mrms test-mrms-ensemble test-go test-python test-web lint build build-linux build-infrastructure-linux export-postgres-image export-python-image export-node-exporter-image build-worker-linux deploy-up dev-up dev-down smoke infrastructure-smoke control-plane-smoke worker-smoke radar-decode-smoke radar-health-smoke radar-qc-smoke radar-grid-smoke ancillary-plan ancillary-download ancillary-verify mrms-download mrms-verify mrms-training-audit mrms-pilot-plan mrms-pilot-run mrms-pilot-validate mrms-holdout-select mrms-conformance mrms-hindcast mrms-faults mrms-ensemble-conformance mrms-ensemble-hindcast mrms-ensemble-freeze-gate mrms-nowcastnet-conformance mrms-nowcastnet-hindcast mrms-nowcastnet-freeze-gate
.PHONY: test-nowcastnet-full-samples mrms-full-sample-plan mrms-full-sample-run mrms-full-sample-validate
.PHONY: test-regeneration regenerate

bootstrap:
	@command -v rg >/dev/null || { echo "ripgrep is required" >&2; exit 1; }
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

test: test-structure test-radar-config test-contracts test-infrastructure test-alerting test-operations test-regeneration test-control-plane test-worker-sdk test-radar-decoder test-radar-health test-radar-qc test-radar-grid test-radar-mosaic test-qpe test-diagnostics test-nowcast-input test-pysteps-lk test-pysteps-steps test-probability-calibration test-nowcastnet test-products test-ensemble-products test-ancillary test-grid test-mrms-ensemble test-go test-python test-web

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

test-alerting:
	bash tests/rp029_alerting_test.sh

test-operations:
	bash tests/rp030_operations_test.sh

test-regeneration:
	bash tests/rp044_regeneration_test.sh
	uv run --project algorithms pytest algorithms/tests/test_manual_regeneration.py

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

test-qpe:
	bash tests/rp011_qpe_test.sh
	uv run --project algorithms pytest algorithms/tests/test_qpe.py

test-diagnostics:
	bash tests/rp012_diagnostics_test.sh
	uv run --project algorithms pytest algorithms/tests/test_diagnostics.py

test-nowcast-input:
	bash tests/rp013_nowcast_input_test.sh
	uv run --project algorithms pytest algorithms/tests/test_nowcast_input.py

test-pysteps-lk:
	bash tests/rp014_pysteps_lk_test.sh
	uv run --project algorithms pytest algorithms/tests/test_pysteps_lk.py

test-pysteps-steps:
	uv run --project algorithms pytest algorithms/tests/test_pysteps_steps.py algorithms/tests/test_probabilistic_verification.py

test-probability-calibration:
	uv run --project algorithms pytest algorithms/tests/test_probability_calibration.py

test-nowcastnet:
	uv run --project algorithms pytest algorithms/tests/test_nowcastnet_adapter.py algorithms/tests/test_nowcastnet_tiling.py algorithms/tests/test_nowcastnet_worker.py algorithms/tests/test_mrms_nowcastnet_hindcast.py

test-nowcastnet-training:
	uv run --project algorithms pytest algorithms/tests/test_mrms_training.py

test-nowcastnet-pilot:
	uv run --project algorithms pytest algorithms/tests/test_mrms_pilot.py algorithms/tests/test_mrms_precip.py

test-nowcastnet-full-samples:
	uv run --project algorithms pytest algorithms/tests/test_mrms_full_samples.py

test-products:
	bash tests/rp015_products_test.sh
	uv run --project algorithms pytest algorithms/tests/test_products.py

test-ensemble-products:
	bash tests/rp023_ensemble_products_test.sh
	uv run --project algorithms pytest algorithms/tests/test_ensemble_products.py

test-ancillary:
	uv run --project algorithms pytest algorithms/tests/test_ancillary.py

test-grid:
	uv run --project algorithms pytest algorithms/tests/test_grid.py

test-mrms:
	uv run --project algorithms pytest algorithms/tests/test_mrms_archive.py algorithms/tests/test_mrms_precip.py algorithms/tests/test_mrms_verification_profile.py algorithms/tests/test_verification.py algorithms/tests/test_verification_baselines.py algorithms/tests/test_algorithm_verification_map.py algorithms/tests/test_mrms_hindcast.py algorithms/tests/test_pysteps_lk.py

test-mrms-ensemble:
	uv run --project algorithms pytest algorithms/tests/test_mrms_holdout.py algorithms/tests/test_mrms_ensemble_profile.py algorithms/tests/test_mrms_ensemble_gate.py algorithms/tests/test_mrms_ensemble_hindcast.py algorithms/tests/test_probabilistic_verification.py

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
	go build -buildvcs=false -trimpath -ldflags="$(RAINPULSE_GO_LDFLAGS)" -o .build/rainpulse-api ./services/control/cmd/api
	go build -buildvcs=false -trimpath -ldflags="$(RAINPULSE_GO_LDFLAGS)" -o .build/rainpulse-web ./services/control/cmd/web
	go build -buildvcs=false -trimpath -ldflags="$(RAINPULSE_GO_LDFLAGS)" -o .build/rainpulse-orchestrator ./services/control/cmd/orchestrator
	uv build --project algorithms --out-dir .build/python
	pnpm --filter @rainpulse/web build

regenerate:
	REGEN_PRESET="$(REGEN_PRESET)" \
	REGEN_RUN_ID="$(REGEN_RUN_ID)" \
	REGEN_ISSUE_TIME="$(REGEN_ISSUE_TIME)" \
	REGEN_INPUT_URI="$(REGEN_INPUT_URI)" \
	REGEN_REASON="$(REGEN_REASON)" \
	bash scripts/regenerate_forecasts.sh

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
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath -ldflags="-s -w $(RAINPULSE_GO_LDFLAGS)" -o .build/linux-amd64/rainpulse-api ./services/control/cmd/api
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath -ldflags="-s -w $(RAINPULSE_GO_LDFLAGS)" -o .build/linux-amd64/rainpulse-web ./services/control/cmd/web
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -trimpath -ldflags="-s -w $(RAINPULSE_GO_LDFLAGS)" -o .build/linux-amd64/rainpulse-orchestrator ./services/control/cmd/orchestrator
	pnpm --filter @rainpulse/web build

export-postgres-image:
	mkdir -p .build/tools
	GOBIN="$(CURDIR)/.build/tools" go install github.com/google/go-containerregistry/cmd/crane@$(CRANE_VERSION)
	.build/tools/crane pull --platform linux/amd64 $(POSTGRES_IMAGE) .build/postgres-17.11-alpine3.24-linux-amd64.tar

export-python-image:
	mkdir -p .build/tools
	GOBIN="$(CURDIR)/.build/tools" go install github.com/google/go-containerregistry/cmd/crane@$(CRANE_VERSION)
	.build/tools/crane pull --platform linux/amd64 $(PYTHON_IMAGE) .build/python-3.13.12-slim-bookworm-linux-amd64.tar

export-node-exporter-image:
	mkdir -p .build/tools
	GOBIN="$(CURDIR)/.build/tools" go install github.com/google/go-containerregistry/cmd/crane@$(CRANE_VERSION)
	.build/tools/crane pull --platform linux/amd64 quay.io/prometheus/node-exporter:$(NODE_EXPORTER_VERSION) .build/node-exporter-$(NODE_EXPORTER_VERSION)-linux-amd64.tar

WORKER_WHEEL_PROXY ?=

build-worker-linux:
	mkdir -p .build
	uv export --project algorithms --locked --no-dev --no-emit-project --no-emit-package pysteps --no-emit-package opencv-python-headless --no-emit-package scipy --format requirements.txt --output-file .build/worker-requirements.txt
	rm -rf .build/worker-site-packages
	uv pip install --target .build/worker-site-packages --requirements .build/worker-requirements.txt --python-version 3.13.12 --python-platform x86_64-manylinux_2_28 --only-binary :all: --no-binary asciitree,jsmin
	RAINPULSE_DOWNLOAD_PROXY="$(WORKER_WHEEL_PROXY)" bash scripts/stage_linux_nowcast_wheels.sh .build/wheelhouse .build/worker-site-packages
	bash scripts/stage_pysteps_runtime.sh .build/worker-site-packages

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

MRMS_ROOT ?= runtime/datasets/mrms
MRMS_START ?= 2021-08-01
MRMS_END ?= 2021-08-31
MRMS_CADENCE_MINUTES ?= 10
MRMS_WORKERS ?= 8
MRMS_PROXY ?=
MRMS_PROFILE ?= configs/verification/rp016-mrms-v1.yaml
MRMS_REPORT_ROOT ?= runtime/reports/mrms
MRMS_CASE ?=
MRMS_MAX_ISSUES ?=
MRMS_RUN_ID ?=
MRMS_SKIP_HASH ?= 0
MRMS_HOLDOUT_CATALOG ?= configs/verification/mrms-holdout-regions-v1.yaml
MRMS_HOLDOUT_MONTHS ?= 2024-06 2025-01
MRMS_HOLDOUT_OUTPUT ?= runtime/reports/mrms/rp021-mrms-holdout-selection-v1.json
MRMS_ENSEMBLE_PROFILE ?= configs/verification/rp024-mrms-ensemble-v1.yaml
MRMS_ENSEMBLE_SPLIT ?= development
MRMS_DEVELOPMENT_SUMMARY ?=
MRMS_ENSEMBLE_GATE_OUTPUT ?= configs/verification/rp024-development-gate-v1.json
MRMS_NOWCASTNET_PROFILE ?= configs/verification/rp026-mrms-nowcastnet-v1.yaml
MRMS_NOWCASTNET_SPLIT ?= development
MRMS_NOWCASTNET_CAPSULE_ROOT ?= /opt/rainpulse/nowcastnet/official-v1
MRMS_NOWCASTNET_DEVICE ?= cuda:0
MRMS_NOWCASTNET_GATE_OUTPUT ?= configs/verification/rp026-development-gate-v1.json
NOWCASTNET_TRAINING_PROFILE ?= configs/training/nowcastnet-mrms-training-v1.yaml
NOWCASTNET_TRAINING_AUDIT_OUTPUT ?= runtime/training/nowcastnet-mrms-v1/audit
NOWCASTNET_TRAINING_AUDIT_START ?=
NOWCASTNET_TRAINING_AUDIT_END ?=
NOWCASTNET_TRAINING_FULL_HASH ?= 0
NOWCASTNET_PILOT_PROFILE ?= configs/training/nowcastnet-mrms-pilot-v1.yaml
NOWCASTNET_PILOT_AUDIT_ROOT ?= runtime/training/nowcastnet-mrms-v1/audit
NOWCASTNET_PILOT_PLAN ?= runtime/training/nowcastnet-mrms-v1/pilot-plan-v1.json
NOWCASTNET_PILOT_OUTPUT ?= runtime/training/nowcastnet-mrms-v1/pilot-v1
NOWCASTNET_PILOT_WORKERS ?= 1
NOWCASTNET_PILOT_MAX_WINDOWS ?=
NOWCASTNET_PILOT_VALIDATION_SAMPLES ?= 64
NOWCASTNET_PILOT_SKIP_CONTENT_HASH ?= 0
NOWCASTNET_FULL_SAMPLE_PROFILE ?= configs/training/nowcastnet-mrms-full-samples-v1.yaml
NOWCASTNET_FULL_SAMPLE_AUDIT_ROOT ?= runtime/training/nowcastnet-mrms-v1/audit
NOWCASTNET_FULL_SAMPLE_PLAN ?= runtime/training/nowcastnet-mrms-v1/full-sample-plan-v1.json
NOWCASTNET_FULL_SAMPLE_WORKERS ?= 1
NOWCASTNET_FULL_SAMPLE_MAX_WINDOWS ?=
NOWCASTNET_FULL_SAMPLE_VALIDATION_SAMPLES ?= 64
NOWCASTNET_FULL_SAMPLE_EXPECTED_WINDOWS ?=
NOWCASTNET_FULL_SAMPLE_ALLOW_PARTIAL ?= 0
NOWCASTNET_FULL_SAMPLE_SKIP_CONTENT_HASH ?= 0

mrms-download:
	uv run --project algorithms python -m rainpulse_algo.datasets.mrms_archive download --start $(MRMS_START) --end $(MRMS_END) --root $(MRMS_ROOT) --cadence-minutes $(MRMS_CADENCE_MINUTES) --workers $(MRMS_WORKERS) $(if $(MRMS_PROXY),--proxy $(MRMS_PROXY),)

mrms-verify:
	uv run --project algorithms python -m rainpulse_algo.datasets.mrms_archive verify --start $(MRMS_START) --end $(MRMS_END) --root $(MRMS_ROOT) --cadence-minutes $(MRMS_CADENCE_MINUTES) --full-hash

mrms-training-audit:
	uv run --project algorithms python -m rainpulse_algo.datasets.mrms_training audit --profile $(NOWCASTNET_TRAINING_PROFILE) --dataset-root $(MRMS_ROOT) --output-root $(NOWCASTNET_TRAINING_AUDIT_OUTPUT) $(if $(NOWCASTNET_TRAINING_AUDIT_START),--start $(NOWCASTNET_TRAINING_AUDIT_START),) $(if $(NOWCASTNET_TRAINING_AUDIT_END),--end $(NOWCASTNET_TRAINING_AUDIT_END),) $(if $(filter 1,$(NOWCASTNET_TRAINING_FULL_HASH)),--full-hash,)

mrms-pilot-plan:
	uv run --project algorithms python -m rainpulse_algo.datasets.mrms_pilot plan --repository-root $(CURDIR) --pilot-profile $(NOWCASTNET_PILOT_PROFILE) --audit-root $(NOWCASTNET_PILOT_AUDIT_ROOT) --output $(NOWCASTNET_PILOT_PLAN)

mrms-pilot-run:
	uv run --project algorithms python -m rainpulse_algo.datasets.mrms_pilot run --repository-root $(CURDIR) --pilot-profile $(NOWCASTNET_PILOT_PROFILE) --audit-root $(NOWCASTNET_PILOT_AUDIT_ROOT) --plan $(NOWCASTNET_PILOT_PLAN) --dataset-root $(MRMS_ROOT) --output-root $(NOWCASTNET_PILOT_OUTPUT) --workers $(NOWCASTNET_PILOT_WORKERS) $(if $(NOWCASTNET_PILOT_MAX_WINDOWS),--max-windows $(NOWCASTNET_PILOT_MAX_WINDOWS),)

mrms-pilot-validate:
	uv run --project algorithms python -m rainpulse_algo.datasets.mrms_pilot validate --repository-root $(CURDIR) --pilot-profile $(NOWCASTNET_PILOT_PROFILE) --plan $(NOWCASTNET_PILOT_PLAN) --output-root $(NOWCASTNET_PILOT_OUTPUT) --random-samples $(NOWCASTNET_PILOT_VALIDATION_SAMPLES) $(if $(filter 1,$(NOWCASTNET_PILOT_SKIP_CONTENT_HASH)),--skip-content-hash,)

mrms-full-sample-plan:
	uv run --project algorithms python -m rainpulse_algo.datasets.mrms_full_samples plan --repository-root $(CURDIR) --profile $(NOWCASTNET_FULL_SAMPLE_PROFILE) --audit-root $(NOWCASTNET_FULL_SAMPLE_AUDIT_ROOT) --output $(NOWCASTNET_FULL_SAMPLE_PLAN)

mrms-full-sample-run:
	uv run --project algorithms python -m rainpulse_algo.datasets.mrms_full_samples run --repository-root $(CURDIR) --profile $(NOWCASTNET_FULL_SAMPLE_PROFILE) --audit-root $(NOWCASTNET_FULL_SAMPLE_AUDIT_ROOT) --plan $(NOWCASTNET_FULL_SAMPLE_PLAN) --workers $(NOWCASTNET_FULL_SAMPLE_WORKERS) $(if $(NOWCASTNET_FULL_SAMPLE_MAX_WINDOWS),--max-windows $(NOWCASTNET_FULL_SAMPLE_MAX_WINDOWS),)

mrms-full-sample-validate:
	uv run --project algorithms python -m rainpulse_algo.datasets.mrms_full_samples validate --repository-root $(CURDIR) --profile $(NOWCASTNET_FULL_SAMPLE_PROFILE) --plan $(NOWCASTNET_FULL_SAMPLE_PLAN) --random-samples $(NOWCASTNET_FULL_SAMPLE_VALIDATION_SAMPLES) $(if $(filter 1,$(NOWCASTNET_FULL_SAMPLE_ALLOW_PARTIAL)),--allow-partial --expected-windows $(NOWCASTNET_FULL_SAMPLE_EXPECTED_WINDOWS),) $(if $(filter 1,$(NOWCASTNET_FULL_SAMPLE_SKIP_CONTENT_HASH)),--skip-content-hash,)

mrms-holdout-select:
	uv run --project algorithms python -m rainpulse_algo.verification.mrms_holdout --root $(MRMS_ROOT) --catalog $(MRMS_HOLDOUT_CATALOG) $(foreach month,$(MRMS_HOLDOUT_MONTHS),--month $(month)) --output $(MRMS_HOLDOUT_OUTPUT)

mrms-conformance:
	uv run --project algorithms python -m rainpulse_algo.verification.mrms_hindcast conformance --repository-root $(CURDIR) --profile $(MRMS_PROFILE) --root $(MRMS_ROOT) $(if $(MRMS_CASE),--case $(MRMS_CASE),) $(if $(MRMS_MAX_ISSUES),--max-issues $(MRMS_MAX_ISSUES),) $(if $(filter 1,$(MRMS_SKIP_HASH)),--skip-hash,)

mrms-hindcast:
	uv run --project algorithms python -m rainpulse_algo.verification.mrms_hindcast hindcast --repository-root $(CURDIR) --profile $(MRMS_PROFILE) --root $(MRMS_ROOT) --output-root $(MRMS_REPORT_ROOT) $(if $(MRMS_CASE),--case $(MRMS_CASE),) $(if $(MRMS_MAX_ISSUES),--max-issues $(MRMS_MAX_ISSUES),) $(if $(MRMS_RUN_ID),--run-id $(MRMS_RUN_ID),) $(if $(filter 1,$(MRMS_SKIP_HASH)),--skip-hash,)

mrms-ensemble-conformance:
	uv run --project algorithms python -m rainpulse_algo.verification.mrms_ensemble_hindcast conformance --repository-root $(CURDIR) --profile $(MRMS_ENSEMBLE_PROFILE) --split $(MRMS_ENSEMBLE_SPLIT) --root $(MRMS_ROOT) $(if $(MRMS_CASE),--case $(MRMS_CASE),) $(if $(MRMS_MAX_ISSUES),--max-issues $(MRMS_MAX_ISSUES),) $(if $(filter 1,$(MRMS_SKIP_HASH)),--skip-hash,)

mrms-ensemble-hindcast:
	uv run --project algorithms python -m rainpulse_algo.verification.mrms_ensemble_hindcast hindcast --repository-root $(CURDIR) --profile $(MRMS_ENSEMBLE_PROFILE) --split $(MRMS_ENSEMBLE_SPLIT) --root $(MRMS_ROOT) --output-root $(MRMS_REPORT_ROOT) $(if $(MRMS_CASE),--case $(MRMS_CASE),) $(if $(MRMS_MAX_ISSUES),--max-issues $(MRMS_MAX_ISSUES),) $(if $(MRMS_RUN_ID),--run-id $(MRMS_RUN_ID),) $(if $(filter 1,$(MRMS_SKIP_HASH)),--skip-hash,)

mrms-ensemble-freeze-gate:
	@test -n "$(MRMS_DEVELOPMENT_SUMMARY)" || { echo "set MRMS_DEVELOPMENT_SUMMARY to a completed development summary.json" >&2; exit 1; }
	uv run --project algorithms python -m rainpulse_algo.verification.mrms_ensemble_gate --repository-root $(CURDIR) --profile $(MRMS_ENSEMBLE_PROFILE) --development-summary $(MRMS_DEVELOPMENT_SUMMARY) --output $(MRMS_ENSEMBLE_GATE_OUTPUT)

mrms-nowcastnet-conformance:
	uv run --project algorithms python -m rainpulse_algo.verification.mrms_nowcastnet_hindcast conformance --repository-root $(CURDIR) --profile $(MRMS_NOWCASTNET_PROFILE) --split $(MRMS_NOWCASTNET_SPLIT) --root $(MRMS_ROOT) $(if $(MRMS_CASE),--case $(MRMS_CASE),) $(if $(MRMS_MAX_ISSUES),--max-issues $(MRMS_MAX_ISSUES),) $(if $(filter 1,$(MRMS_SKIP_HASH)),--skip-hash,)

mrms-nowcastnet-hindcast:
	uv run --project algorithms python -m rainpulse_algo.verification.mrms_nowcastnet_hindcast hindcast --repository-root $(CURDIR) --profile $(MRMS_NOWCASTNET_PROFILE) --split $(MRMS_NOWCASTNET_SPLIT) --root $(MRMS_ROOT) --output-root $(MRMS_REPORT_ROOT) --capsule-root $(MRMS_NOWCASTNET_CAPSULE_ROOT) --device $(MRMS_NOWCASTNET_DEVICE) $(if $(MRMS_CASE),--case $(MRMS_CASE),) $(if $(MRMS_MAX_ISSUES),--max-issues $(MRMS_MAX_ISSUES),) $(if $(MRMS_RUN_ID),--run-id $(MRMS_RUN_ID),) $(if $(filter 1,$(MRMS_SKIP_HASH)),--skip-hash,)

mrms-nowcastnet-freeze-gate:
	@test -n "$(MRMS_DEVELOPMENT_SUMMARY)" || { echo "set MRMS_DEVELOPMENT_SUMMARY to a completed development summary.json" >&2; exit 1; }
	uv run --project algorithms python -m rainpulse_algo.verification.mrms_nowcastnet_gate --repository-root $(CURDIR) --profile $(MRMS_NOWCASTNET_PROFILE) --development-summary $(MRMS_DEVELOPMENT_SUMMARY) --output $(MRMS_NOWCASTNET_GATE_OUTPUT)

mrms-faults:
	uv run --project algorithms pytest algorithms/tests/test_mrms_archive.py::test_verify_checks_size_and_optional_hash algorithms/tests/test_mrms_precip.py::test_reader_crops_to_ascending_grid_and_preserves_mrms_source_states algorithms/tests/test_mrms_hindcast.py::test_archive_source_rejects_a_missing_required_source_slot algorithms/tests/test_mrms_hindcast.py::test_archive_source_checks_manifest_hash_before_grib_decode algorithms/tests/test_pysteps_lk.py::test_uses_explicit_zero_motion_fallback_for_no_rain algorithms/tests/test_pysteps_lk.py::test_empty_motion_domain_has_specific_zero_motion_fallback

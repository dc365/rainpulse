# RainPulse implementation status

Updated: 2026-08-24

## Completed acceptance targets

RP-000 repository baseline is implemented:

- pnpm workspace with a React/TypeScript/Vite Web application;
- Go control API exposing `GET /api/v1/system/status`;
- Go Web gateway serving the React SPA and proxying `/api/*` to the control API;
- Python `rainpulse_algo` package baseline managed by uv;
- repository structure checks, unit tests, linting, builds, Compose health checks, and an end-to-end smoke command;
- CI workflow for the same bootstrap, test, lint, and build seams.

RP-001 contracts are implemented:

- `contracts/openapi.yaml` covers the planned `/api/v1` run, product,
  verification, system, SSE and administration operations;
- Draft 2020-12 Schemas and valid examples cover `job.requested`,
  `job.completed`, `job.failed`, and `product.published` events;
- canonical NowcastInput and ForecastOutput Zarr contracts preserve valid
  no-rain, missing and low-quality states;
- pinned `oapi-codegen` and `openapi-typescript` generation produces the Go
  Chi server interface/models and TypeScript API types;
- Go and React consume the generated `SystemStatus` model, and CI rejects
  generated-code drift.

RP-002 infrastructure is implemented:

- Compose runs PostgreSQL 17.11, NATS 2.14.5 with persistent JetStream, and
  MinIO `RELEASE.2025-10-15T17-29-55Z` with a persistent `rainpulse` bucket;
- the initial UTC PostgreSQL migration creates all 14 Phase 1 metadata tables,
  their traceability/quality/status constraints, indexes, and an idempotent
  `schema_migrations` ledger;
- infrastructure ports bind to loopback by default, while API and Web retain
  their existing deployment-host endpoints;
- required secrets live only in the ignored `deploy/.env`; normal shutdown
  does not remove PostgreSQL, JetStream, or MinIO volumes;
- Compose dependency gates, service health checks, migration reruns, bucket
  initialization, infrastructure smoke tests, and API/Web smoke tests pass on
  the GPU test server without consuming GPU resources.

RP-003 Go run/job control plane is implemented:

- a long-lived Go orchestrator owns the run/job state machines and health
  endpoint; the API remains a separate Go process and React still calls Go only;
- run, job, and `job.requested` outbox records are created in one PostgreSQL
  transaction; publishing uses event UUIDs as JetStream message IDs and marks a
  job `RUNNING` only after the broker acknowledges the task;
- terminal Worker results are strictly decoded against their frozen event
  shapes, consumed through `rainpulse-orchestrator-results-v2`, and applied to
  job/run state in the same transaction as the inbox ledger;
- terminal results are idempotent by `event_id` and `job_id`, so duplicate or
  conflicting success/failure events cannot terminate one job twice;
- `GET /runs/latest`, `GET /runs`, `GET /runs/{run_id}`,
  `GET /runs/{run_id}/jobs`, manual rerun, and run-filtered SSE are backed by
  PostgreSQL and the generated OpenAPI models;
- pgx `v5.10.0` and nats.go `v1.53.1` are pinned; API readiness now verifies the
  database state rather than accepting an HTTP response alone;
- the server smoke test passes a real create → outbox → JetStream → completion
  → inbox → state update → rerun → SSE loop, including duplicate completion.

RP-004 Python Worker SDK is implemented:

- the long-lived simulation Worker uses a durable JetStream pull consumer and
  strict Pydantic models for `job.requested`, `job.completed`, and `job.failed`;
- a deterministic result event UUID and committed object marker provide
  idempotency across request redelivery, while the Go inbox is the final
  job-level idempotency boundary;
- outputs are written below `_temporary`, size-validated, copied to the final
  prefix, and committed by writing `forecast.zarr/_SUCCESS.json` last; a task is
  ACKed only after its completion/failure result is acknowledged by JetStream;
- poison messages are terminated, while result-publication failure NAKs the
  original request so it can be redelivered;
- the Worker has a dependency-aware health endpoint and uses a dedicated MinIO
  application user instead of root credentials;
- the server smoke covers health, successful execution, committed marker,
  request replay without duplicate output/inbox state, and failure propagation
  into `FAILED` run/job state with a structured error code.

The RP-003/RP-004 model/config records, output objects, and metrics are explicitly
simulations. They prove the distributed execution path only and do not claim
pySTEPS or real-data readiness.

This remains an engineering/contract baseline, not a real-data nowcasting loop.
Concrete radar/QPE source and target-grid registrations must be frozen before
RP-005 real-sample standardization, not invented during infrastructure work.

## Deployment decision

The GPU test server cannot currently reach Docker Hub reliably and does not
provide a Go, Node, or Python build toolchain. API, Web, NATS, MinIO, and the
MinIO client are locally cross-compiled for Linux/amd64 and packaged into
`scratch` images. Worker dependencies are resolved from the lockfile into a
Linux/amd64 site-packages directory. The pinned official PostgreSQL and Python
base images are exported through the workstation proxy and imported with
`docker load`; the server Docker daemon configuration is left unchanged.

Reproducible commands:

```bash
make build-linux
make build-worker-linux
# If the target cannot reach Docker Hub:
HTTPS_PROXY=http://127.0.0.1:7897 make export-postgres-image
HTTPS_PROXY=http://127.0.0.1:7897 make export-python-image
# synchronize the repository and generated artifacts to the target
make deploy-up
make infrastructure-smoke
make control-plane-smoke
make worker-smoke
make smoke
```

## Active test environment

- Target: `private-test-host`
- Project: `<remote-project-dir>`
- Web: `http://private-test-host:4173`
- API: `http://private-test-host:8080/api/v1/system/status`
- Deployed version: `rp004-20260824`
- Previous remote contents: `<remote-legacy-archive>`

No credentials or secrets belong in this document or repository.

## Next acceptance target

RP-005 standardizes one representative real radar/QPE sample into the frozen
NowcastInput Zarr contract, including missing/no-rain/low-quality semantics and
quality summary. It cannot start honestly until the source and target-grid
decisions below are supplied and frozen.

## Decisions required before RP-005

Before real-sample standardization begins, freeze:

1. Radar and QPE source, format, variables, cadence, sample files, and delivery mechanism.
2. Target grid CRS, bounds, resolution, masks, and display timezone.
3. Missing-data, no-rain, quality-index, and QC threshold semantics.
4. Sanitized representative samples and expected normalized outputs for contract tests.

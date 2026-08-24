# RainPulse implementation status

Updated: 2026-08-24

## Active baseline

The implementation and acceptance baseline is
`docs/RainPulse_技术架构与实施方案_含雷达质控_v1.1.md`.
`docs/RainPulse_技术架构与实施方案.md` is retained only as the superseded v1.0
history. The active chain starts with immutable raw polar radar base data:

```text
RawRadarAsset
→ NormalizedRadarVolume
→ QCRadarVolume
→ RadarGrid / Hybrid Scan
→ RadarAnalysis / QI mosaic / QPE
→ NowcastInput
→ pySTEPS
→ products and verification
```

No real nowcast may bypass polar QC, mosaic/QPE quality gates, or the fixed-step
NowcastInput gate.

## v1.1 task status

| Task | Status | Current capability |
|---|---|---|
| RP-000 Monorepo/CI | Complete | React, Go, Python, unified commands, CI and smoke seams |
| RP-001 radar inventory/config | Complete | Draft/ready radar Schema, inventory template, unknown-value gate and uint32 QC flags |
| RP-002 data/event contracts | Complete | Raw, normalized polar, QC polar, Hybrid Scan, analysis, nowcast and forecast contracts plus domain event schemas/examples |
| RP-003 infrastructure | Complete | PostgreSQL, NATS JetStream and MinIO with migrations, persistence and health checks |
| RP-004 Go three-level workflows | Implemented locally; server acceptance pending | Separate radar-scan, analysis-cycle and forecast states; additive metadata migration; radar/analysis API and SSE; two-radar degradation simulation |
| RP-005 Python Worker SDK | Foundation complete | NATS/Pydantic/idempotency/atomic output/health are proven by a simulation Worker; domain-task routing still needs generalization |
| RP-006 first real radar decoder | Blocked on input | Requires a verified representative raw base-data sample and complete ready radar configuration |
| RP-007–RP-016 | Not started | Depend on the preceding radar chain and representative data |

The old execution labels map to v1.1 by capability, not by their previous
number: old contract work contributes to RP-002, old infrastructure is RP-003,
old control-plane work is an RP-004 foundation, and the old simulated Worker is
the RP-005 foundation.

## Completed engineering foundation

RP-000 provides:

- pnpm React/TypeScript/Vite Web workspace;
- Go API, orchestrator and Web gateway;
- Python `rainpulse_algo` package managed by uv;
- repository, unit, lint, build, Compose and smoke commands;
- CI for the same seams.

RP-001 now provides:

- `configs/schemas/radar-config.schema.json` with strict unknown-key rejection;
- a draft radar inventory template that retains unknown values explicitly;
- a `ready` gate requiring verified station, hardware, geometry, DBZH mapping,
  source, UTC, ancillary and QC versions;
- canonical optional moment names without synthesizing missing fields;
- a versioned uint32 QC flag bit set and tests for unique bits/masks.

RP-002 now provides:

- immutable `RawRadarAsset` metadata;
- original-geometry `NormalizedRadarVolume`;
- polar `QCRadarVolume` with QI components, module provenance and uint32 flags;
- single-radar `RadarGrid`/Hybrid Scan and multi-radar `RadarAnalysis`;
- `NowcastInput` v1.1 provenance and unchanged `ForecastOutput` semantics;
- radar receive/decode/QC/grid, analysis-cycle/mosaic, input-ready and
  forecast-run event schemas with valid examples;
- existing common job command/result and product-published schemas;
- contract tests preserving valid no-rain, missing and low-quality states.

RP-003 remains operational:

- PostgreSQL 17.11;
- NATS 2.14.5 with persistent JetStream;
- MinIO with a persistent `rainpulse` bucket;
- UTC migrations, application credentials, health gates and smoke tests.

RP-004 now adds locally verified control-plane behavior:

- a generic `workflow_runs` identity keeps shared jobs referentially sound
  without mixing the three domain state machines;
- independent `radar_scan_run` and `analysis_cycle` records and transitions;
- radar/config/scan, analysis-cycle and actual-contributor metadata tables;
- radar registration/status, scan and analysis REST queries;
- forecast, radar-scan and analysis-cycle SSE filters;
- a deterministic two-radar simulation where radar A reaches
  `RADAR_GRID_READY`, radar B reaches `FAILED`, and the analysis still reaches
  `ANALYSIS_READY` with a degradation reason and one actual contributor.

The RP-004/RP-005 foundation also proves:

- transactional run/job/outbox creation;
- JetStream publication and strict terminal-result consumption;
- inbox/job idempotency under at-least-once delivery;
- long-lived pull consumers;
- temporary output, validation, final copy and `_SUCCESS.json` commit;
- success, replay, poison-message and structured-failure behavior.

All existing model/config records and products are simulations. They do not
claim radar decode, QC, QPE, pySTEPS, or real-data readiness.

## Active test environment

- Target: `private-test-host`
- Project: `<remote-project-dir>`
- Web: `http://private-test-host:4173`
- API: `http://private-test-host:8080/api/v1/system/status`
- Deployed runtime version: `rp004-20260824`
- Previous remote contents: `<remote-legacy-archive>`

The target has 24 logical CPUs, about 156 GiB RAM, an RTX 6000D GPU, NVIDIA
container runtime, and about 405 GB free disk at the last audit. Phase 1 radar
QC and pySTEPS are CPU-first. GPU use is reserved for later NowcastNet work.
Raw-volume retention and expected daily volume must be frozen before continuous
ingest because free disk is the immediate capacity constraint.

Docker Hub access is unreliable. Continue to use local Linux/amd64 builds and
export/import pinned images through `http://127.0.0.1:7897` when necessary.
No credentials or secrets belong in this document or repository.

The RP-004 code, generated clients, tests and Linux/amd64 binaries pass locally.
The workstation has no Docker runtime, and the GPU server rejected the supplied
SSH authentication on 2026-08-24. Migration `0005_radar_workflows.sql` and the
updated Compose control-plane smoke therefore remain pending on-server
acceptance. The deployed runtime remains `rp004-20260824`; no remote state or
volume was changed by this attempt.

## Next acceptance target

First complete the pending RP-004 server acceptance:

1. restore SSH access to the test server;
2. synchronize the committed source and Linux/amd64 artifacts;
3. apply additive migration `0005_radar_workflows.sql` on the persistent test database;
4. run infrastructure, forecast control-plane, two-radar degradation, Worker
   and end-to-end smoke tests;
5. retain the previous deployed version until these checks pass.

Then complete RP-005 domain-task routing so decode, QC, grid, mosaic/QPE and
NowcastInput workers reuse the established long-lived Worker SDK without
pretending to implement real meteorological algorithms.

## Required inputs before RP-006

For at least one physical radar, provide and verify:

1. Full-volume raw base-data samples, source format/version and delivery path.
2. Site longitude/latitude/altitude plus altitude datum.
3. Radar model/band/beam width and calibration information.
4. Scan strategy, elevations, update interval, azimuth/range resolution and
   maximum range.
5. Field names, source units, scale/offset, missing values and dual-polarization
   availability.
6. Timestamp timezone and filename/time semantics.
7. Target grid CRS, bounds, resolution and masks.
8. DEM and coastline assets with versions and datum metadata.
9. Representative clear-air, radial-interference, sea/AP, mountain-blockage,
   ordinary-rain, convection and typhoon cases.
10. Later QPE truth: gauge locations, observation interval and quality rules.

Until these inputs are available, RP-004 and RP-005 can progress with explicitly
synthetic fixtures, but RP-006 must not invent a decoder or production radar
configuration.

## Reproducible commands

```bash
make test-radar-config
make test-contracts
make test
make lint
make build

# Deployment artifacts, when runtime code changes:
make build-linux
make build-worker-linux
HTTPS_PROXY=http://127.0.0.1:7897 make export-postgres-image
HTTPS_PROXY=http://127.0.0.1:7897 make export-python-image
make deploy-up
make infrastructure-smoke
make control-plane-smoke
make worker-smoke
make smoke
```

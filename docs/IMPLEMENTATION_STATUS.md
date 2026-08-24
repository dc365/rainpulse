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
| RP-004 Go three-level workflows | Complete | Separate radar-scan, analysis-cycle and forecast states; additive metadata migration; radar/analysis API and SSE; two-radar degradation simulation |
| RP-005 Python Worker SDK | Complete | Registered decode/QC/grid/mosaic-QPE/NowcastInput profiles reuse strict contracts, idempotency, artifact-specific atomic output, logs and health |
| RP-006 first real radar decoder | Complete | CMA RSTM 2.0 decoder, Z9598 draft config, sweep-group Zarr, real Worker profile and NAS golden-sample acceptance |
| RP-007–RP-016 | Not started | Next is data integrity/radar health, followed by QC and the remaining radar/nowcast chain |

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

RP-004 now adds locally and server-verified control-plane behavior:

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

RP-005 now additionally provides:

- a registered `TaskHandler` boundary with one request model, subject, durable
  consumer, executor and output artifact definition per Worker profile;
- strict Pydantic models for decode, QC, grid, mosaic/QPE and NowcastInput task
  requests, including 3–6-frame identity checks;
- artifact-specific atomic markers for `volume.zarr`, `grid.zarr`,
  `analysis.zarr`, `input.zarr` and the existing `forecast.zarr`;
- deterministic replay behavior across all synthetic domain profiles;
- explicit fixture output stating that no radar array or meteorological
  algorithm ran.

RP-006 now provides:

- a streaming decoder for raw or whole-file BZip2 CMA RSTM Level 2 version 2.0;
- strict site, scan, cut, radial, geometry, scale/offset and source checksum
  validation;
- preservation of the original per-cut polar geometry in Zarr v2
  `sweep_groups_v1`;
- verified Z9598 mappings for DBZH, ZDR, RHOHV, PHIDP, VR, SW and SNR, with
  source reserved codes 0–4 decoded as NaN;
- a real `radar-decode-fmt` Worker restricted to configured read-only
  `file://` roots and a separate synthetic decode subject;
- multi-object atomic publication with a deterministic manifest and
  `_SUCCESS.json` written last;
- a reproducible NAS golden sample and acceptance script, documented in
  `docs/RP006_真实雷达基数据解码验收记录.md`.

Z9598 is a real-sample configuration, but it intentionally remains `draft` and
its decoder output is marked `operational_eligible=false`. QC, gridding, QPE,
NowcastInput and forecast products remain synthetic until their corresponding
tasks are implemented.

## Active test environment

- Target: `private-test-host`
- Project: `<remote-project-dir>`
- Web: `http://private-test-host:4173`
- API: `http://private-test-host:8080/api/v1/system/status`
- Deployed runtime version: `rp006-v1.1-7903196-20260824`
- One-time legacy archive: `<remote-legacy-archive>`

The target has 24 logical CPUs, about 156 GiB RAM, an RTX 6000D GPU, NVIDIA
container runtime, and about 405 GB free disk at the last audit. Phase 1 radar
QC and pySTEPS are CPU-first. GPU use is reserved for later NowcastNet work.
Raw-volume retention and expected daily volume must be frozen before continuous
ingest because free disk is the immediate capacity constraint.

Docker Hub access is unreliable. Continue to use local Linux/amd64 builds and
export/import pinned images through `http://127.0.0.1:7897` when necessary.
No credentials or secrets belong in this document or repository.

The RP-004–RP-006 code, generated clients, tests and Linux/amd64 binaries pass
locally. SSH public-key access to the GPU server is active; passwords are not
stored locally. On 2026-08-24 RP-006 was deployed in place without a new source
or database backup. All services, including `radar-decode-worker`, are healthy;
the Z9598 NAS golden sample decoded to 11 sweeps, 3994 rays and seven canonical
fields and passed the normalized Zarr validator. Infrastructure, control-plane,
partial-radar degradation, Worker idempotency/failure, API and Web smoke tests
also passed. The retained PostgreSQL, NATS and MinIO volumes were not deleted.

The local repository is the code source of truth. Deployment and integration
debugging run directly in the server's `rainpulse-nowcast` directory through
the formal launch period. Do not create a source or database backup for every
test release; keep only the one-time legacy archive above unless a backup is
explicitly requested. Persistent PostgreSQL, NATS and MinIO volumes remain part
of the active test environment and must not be deleted during ordinary updates.

## Next acceptance target

RP-007 is next: compute data-integrity summaries and radar health from decoded
polar volumes. It will detect missing cuts/radials and field availability,
summarize noise/anomalous values, persist health state, and expose the results
through the Go API and React overview. It must continue to use internal RSTM
radial UTC as observation time because the current NAS is a replay source whose
filename timestamps and payload identities are not unique.

## Required inputs before operational QC and gridding

The raw sample, RSTM 2.0 format, Z9598 header geometry, field scaling and NAS
path are now known. The following still require business or radar-maintainer
confirmation:

1. Z9598 ground/antenna altitude datum and authoritative station metadata.
2. Radar manufacturer/model, formal meaning of RDA type code 4, calibration
   offsets, calibration interval and maintenance metadata.
3. Real-time delivery SLA, filename timestamp semantics and duplicate/replay
   handling policy.
4. Operational integrity thresholds for missing cuts/radials, field coverage,
   noise and anomalous values.
5. Target grid CRS, bounds, resolution and masks.
6. DEM, coastline, static clutter and beam-blockage assets with versions and
   datum metadata.
7. Representative clear-air, radial-interference, sea/AP, mountain-blockage,
   ordinary-rain, convection and typhoon cases.
8. Later QPE truth: gauge locations, observation interval and quality rules.

Until the ready-state metadata and QC versions are verified, the deployed
decoder remains suitable for replay/integration work only and cannot feed an
operational nowcast.

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
make radar-decode-smoke
make smoke
```

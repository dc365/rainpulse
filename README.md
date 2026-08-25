# RainPulse

RainPulse is a 0–2 hour precipitation nowcasting system. The repository keeps the React user interface, Go control plane, Python compute workers, contracts, configuration, and deployment assets together so their boundaries remain explicit and testable.

The implementation baseline is [`docs/RainPulse_技术架构与实施方案_含雷达质控_v1.1.md`](docs/RainPulse_技术架构与实施方案_含雷达质控_v1.1.md). Phase 1 first builds a trusted radar field from immutable raw polar volumes, then delivers a deterministic pySTEPS-LK loop before probabilistic ensembles or NowcastNet enter the production path.

## Repository areas

- `apps/web`: React and TypeScript user interface.
- `services/control`: Go API, orchestration, and ingest control plane.
- `algorithms`: Python compute package and long-lived workers.
- `contracts`: OpenAPI, event Schemas, meteorological data contracts, examples,
  and generated-type configuration.
- `configs`: versioned runtime configuration.
- `deploy`: local and test deployment definitions.
- `scripts`: reproducible bootstrap, verification, and smoke commands.

## Developer entry points

Prerequisites: Go 1.25+, Node.js 22+, pnpm 11, Python 3.11+, `uv`, `ruff`, Docker, and Docker Compose. Docker is only required for the composed runtime commands.

```bash
make bootstrap
make contracts-check
make test-radar-config
make test-grid
make test-ancillary
make test
make dev-up
make infrastructure-smoke
make control-plane-smoke
make worker-smoke
make radar-decode-smoke
make radar-health-smoke
make radar-qc-smoke
make test-radar-grid
make test-radar-mosaic
make smoke
```

These commands are established by RP-000 and become stricter as later acceptance targets add real infrastructure and algorithms.

Run `make contracts-generate` after editing `contracts/openapi.yaml`.
`make contracts-check` regenerates into a temporary directory and fails when
checked-in Go or TypeScript types have drifted from the OpenAPI source.

After `make dev-up`, the public seams are:

- Web: `http://127.0.0.1:4173`
- Control API status: `http://127.0.0.1:8080/api/v1/system/status`
- Latest run: `http://127.0.0.1:8080/api/v1/runs/latest`
- Registered radars: `http://127.0.0.1:8080/api/v1/radars`
- Radar scan workflows: `http://127.0.0.1:8080/api/v1/radar-scans`
- Per-scan QC summary: `http://127.0.0.1:8080/api/v1/radar-scans/{scan_id}/qc-summary`
- Per-scan Hybrid Scan summary: `http://127.0.0.1:8080/api/v1/radar-scans/{scan_id}/grid-summary`
- Per-cycle mosaic summary: `http://127.0.0.1:8080/api/v1/analysis-cycles/{analysis_id}/mosaic-summary`
- Radar fleet health: `http://127.0.0.1:8080/api/v1/radars/status`
- Per-radar health: `http://127.0.0.1:8080/api/v1/radars/{radar_id}/status`
- Analysis cycles: `http://127.0.0.1:8080/api/v1/analysis-cycles`
- Run updates: `http://127.0.0.1:8080/api/v1/events/stream`

Use `make dev-down` to stop the composed runtime without deleting its named
volumes. PostgreSQL, NATS JetStream and MinIO are persistent RP-003 services;
RP-005 provides registered long-lived Worker profiles and common idempotent,
atomic publication behavior. RP-006 adds the first real CMA RSTM 2.0 decoder
and a read-only NAS-backed Worker profile. RP-007 adds decoded-volume integrity
and radar-health diagnostics. RP-008 adds health-gated basic polar QC, a
separate version-isolated QC Zarr, flags/QI/provenance, persistence/API support,
and the radar operations console module. Ancillary-dependent clutter and sea/AP
modules still require representative-case acceptance. RP-009 freezes the Phase
1 EPSG:4326 `0.01°` Fuzhou grid and accepted Fujian–Taiwan Copernicus
GLO-30/GSHHG assets, then adds polar DEM blockage, lowest-usable-elevation
Hybrid Scan, RadarGrid v1.3, persistence/API and a real Z9598 engineering
replay. RP-010 adds strict five-minute time alignment and quality-aware
reflectivity mosaics: clearly better inputs are selected, similar-quality
inputs are combined in linear Z, and source/QI/coverage provenance is retained.
The single-real-radar path is accepted for engineering use; real multi-radar
acceptance still requires a second configured radar and synchronized volume.
QPE and downstream meteorological models remain to be implemented.

## Test deployment artifacts

`make build-linux` creates Linux/amd64 static API, Web gateway, health-check,
NATS, MinIO, and MinIO-client binaries plus the compiled React assets. The
project Dockerfiles package those artifacts into dependency-free `scratch`
images, so the test server does not need Go or Node.js.

`make build-worker-linux` resolves the pinned Python runtime dependencies for a
Linux/amd64 target. `make export-python-image` exports the pinned Python base
image for registry-isolated deployment in the same manner as PostgreSQL.

Use `make deploy-up` on a Linux/amd64 target after `.build/linux-amd64` and
`apps/web/dist` have been synchronized and `deploy/.env` has been created from
the example. If the target cannot reach Docker Hub, run
`make export-postgres-image export-python-image`, transfer the resulting tars,
and load them with `docker load` before starting Compose. `make dev-up` combines the local
artifact build and Compose startup for a developer machine with the full
toolchain.

Current implementation and deployment decisions are recorded in
[`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md). The real
RP-008 server acceptance evidence is in
[`docs/RP008_基础极坐标质控验收记录.md`](docs/RP008_基础极坐标质控验收记录.md).
The accepted RP-009 grid/static-source foundation is recorded in
[`docs/RP009_网格与静态基础数据验收记录.md`](docs/RP009_网格与静态基础数据验收记录.md).
The RP-009 DEM blockage and Hybrid Scan replay is recorded in
[`docs/RP009_DEM波束遮挡与HybridScan验收记录.md`](docs/RP009_DEM波束遮挡与HybridScan验收记录.md).
The RP-010 time-aligned, quality-aware mosaic acceptance is recorded in
[`docs/RP010_雷达时空对齐与质量感知拼图验收记录.md`](docs/RP010_雷达时空对齐与质量感知拼图验收记录.md).

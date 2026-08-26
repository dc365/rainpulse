# RainPulse implementation status

Updated: 2026-08-26

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
| RP-005 Python Worker SDK | Complete | Registered decode/QC/grid/mosaic-QPE/NowcastInput/pySTEPS-LK profiles reuse strict contracts, idempotency, artifact-specific atomic output, logs and health |
| RP-006 first real radar decoder | Complete | CMA RSTM 2.0 decoder, Z9598 draft config, sweep-group Zarr, real Worker profile and NAS golden-sample acceptance |
| RP-007 data integrity/radar health | Complete | Versioned health profile, real-volume integrity metrics, persistence/API and responsive React radar console |
| RP-008 basic polar QC | Core vertical slice complete | Real Z9598 normalized Zarr to version-isolated QC Zarr, flags/QI/provenance, persistence/API/console and replay acceptance; ancillary-dependent modules still await operational assets |
| RP-009 | Core vertical slice complete | Versioned polar DEM blockage, lowest-usable-elevation Hybrid Scan and real Z9598 RadarGrid accepted for engineering replay; operational metadata/cases remain gated |
| RP-010 | Core vertical slice complete | Five-minute alignment, closest-grid selection, QI selection/linear-Z blending, RadarMosaic v1.0, persistence/API and real Z9598 single-radar replay accepted; real two-radar replay remains gated by input inventory |
| RP-011 | Core vertical slice complete | Versioned Z–R QPE, RadarAnalysis v1.2, persistence/API and real Z9598 engineering replay accepted; gauge calibration remains gated |
| RP-012 | Core vertical slice complete | Immutable 11-layer grid/PPI diagnostic bundle, controlled PNG API, React evidence workbench and real Z9598 engineering replay accepted |
| RP-013 | Core vertical slice complete | Strict fixed-step NowcastInput, quality/eligibility gates, immutable Zarr, persistence/events and traceable synthetic server acceptance; real sequence acceptance remains gated |
| RP-014 | Core vertical slice complete | Real dense Lucas–Kanade and semi-Lagrangian extrapolation, physical U/V, 24 leads, persistence/translation baselines, immutable ForecastOutput, persistence/events and synthetic server replay; real forecast-skill acceptance remains gated |
| RP-015 | Complete for synthetic vertical acceptance | ForecastOutput-derived PNG/COG/NetCDF products, point/area APIs, product events, server replay and the responsive OpenLayers short-nowcast GIS are accepted; real forecast skill remains gated |
| RP-016 | Not started | Verification, fault injection and end-to-end operational acceptance follow the accepted RP-015 boundary |

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
- `NowcastInput` v1.2 and `ForecastOutput` v1.1 equal-lat/lon semantics;
- `ApplicationProductBundle` 1.0 and product-build request contract;
- radar receive/decode/QC/grid, analysis-cycle/mosaic, input-ready,
  pySTEPS-LK-requested and baseline-ready event schemas with valid examples;
- existing common job command/result and product-published schemas;
- contract tests preserving valid no-rain, missing and low-quality states.

RP-003 remains operational:

- PostgreSQL 17.11;
- NATS 2.14.5 with persistent JetStream for job and product-publication subjects;
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
  `mosaic.zarr`, `analysis.zarr`, `input.zarr` and the existing
  `forecast.zarr` and `application-products`;
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

RP-007 now provides:

- `rp007-integrity-v1` health configuration and a strict JSON Schema;
- expected/actual/missing sweep and radial checks, azimuth-gap checks, field
  presence/finite/out-of-range summaries, layer anomaly checks and RSTM radial
  noise/channel diagnostics;
- a `health/summary.json` artifact and structured completion diagnostics written
  by the real decoder Worker;
- deterministic raw-asset, scan, workflow, job and event identities for real
  decode requests, plus idempotent persistence into `radar_health_metrics`;
- per-radar and fleet status APIs and a responsive React radar-operations
  console with 30-second refresh, completeness, reasons, fields, noise/channel
  state, plus the UI seam later filled by the RP-008 QC module;
- end-to-end health and replay-idempotency smoke tests, documented in
  `docs/RP007_数据完整性与雷达健康验收记录.md`.

RP-008 now provides:

- `rp008-basic-v1` configuration, strict Schema and `qc-flags-v1` uint32 flag
  definitions;
- health-gated basic polar QC that preserves all original sweep geometry,
  including legal cuts without DBZH, and explicitly retains valid, missing,
  no-rain and low-quality semantics;
- radial-interference detection, static-clutter and sea/AP module seams,
  per-module provenance, QI components and aggregate diagnostics;
- a separate, immutable `QCRadarVolume` Zarr whose object prefix includes the
  QC pipeline version, so versioned reruns cannot reuse another job's completion
  marker;
- deterministic QC jobs, transactional config registration, NATS routing,
  idempotent PostgreSQL metrics, QC summary/status APIs and a responsive React
  console module;
- safe versioned retry from a failed QC stage only when the normalized artifact
  and radar-health gate remain valid, while preserving prior job audit records;
- real Z9598 end-to-end acceptance, documented in
  `docs/RP008_基础极坐标质控验收记录.md`.

Z9598 is a real-sample configuration, but it intentionally remains `draft` and
its decoder output is marked `operational_eligible=false`. RP-008 through
RP-012 can therefore run the real sample for controlled replay/integration
acceptance, but it is not an operational QC, grid, mosaic, QPE or diagnostic
feed. Real
multi-radar acceptance, gauge-validated QPE, NowcastInput and forecast products
remain gated or unimplemented until their corresponding tasks are accepted.

RP-009 now provides:

- immutable `fuzhou_118_123_25_27_0p01deg_v1`, EPSG:4326, `0.01°`,
  `501 × 201`, inclusive point-centre coordinates;
- coordinate SHA-256 and a latitude-aware WGS84 metric instead of the old
  projected square-cell `resolution_m` assumption;
- versioned Copernicus GLO-30 and GSHHG 2.3.7 source definitions for
  `114–127°E`, `21–29°N`;
- resumable, locked download and SHA/geospatial verification tooling;
- 62 accepted GLO-30 tiles, 42 source-side ocean slots and full/high GSHHG
  coastlines on the test server, documented in
  `docs/RP009_网格与静态基础数据验收记录.md`;
- distribution contracts for two-dimensional application NetCDF and aligned
  transparent PNG layers, while retaining Zarr as the canonical internal form;
- strict `rp009-hybrid-v1` beam/blockage/selection configuration with trusted
  lazy native DEM sampling and version/SHA/CRS gates;
- 4/3-Earth beam height, circular partial blockage, cumulative polar blockage
  and direct grid-to-polar registration without converting missing coverage to
  no-rain;
- lowest-usable-elevation selection driven by source QC flags, QI, blockage and
  beam height, with RadarGrid v1.3 fields and retained polar/QI diagnostics;
- deterministic grid jobs, version-isolated atomic Zarr publication,
  PostgreSQL metrics, REST summary and replay-idempotent completion;
- a real Z9598 engineering replay documented in
  `docs/RP009_DEM波束遮挡与HybridScan验收记录.md`.

RP-010 now provides:

- a strict five-minute UTC analysis boundary and deterministic closest
  volume-end selection with an absolute `150 s` offset gate;
- time quality and adjusted quality, best-input selection, and QI-squared
  blending for similar inputs after converting dBZ to linear Z;
- propagation of source radar/count, input time, QI components, QC flags,
  blockage, elevation, beam and terrain diagnostics without filling missing
  coverage as no-rain;
- immutable `RadarMosaic` v1.0 Zarr publication, while keeping QPE and
  `RATE_QPE` exclusively in RP-011;
- transactional analysis/mosaic run creation, versioned configuration,
  outbox/inbox replay safety, persisted mosaic diagnostics and a REST summary;
- fixture coverage of genuine two-radar linear-Z blending and a real Z9598
  single-radar replay, documented in
  `docs/RP010_雷达时空对齐与质量感知拼图验收记录.md`.

RP-011 now provides:

- strict `rp011-basic-qpe-v1` configuration for `Z = 200 R^1.6`, a `10 dBZ`
  valid-no-rain boundary, `300 mm/h` cap and explicitly disabled gauge
  adjustment;
- `RATE_QPE` generation that preserves missing, valid no-rain and low-quality
  semantics instead of turning missing coverage into zero rainfall;
- immutable `RadarAnalysis` v1.2 with the full RP-010 mosaic provenance,
  version-isolated atomic Zarr publication and a QPE summary;
- deterministic QPE jobs, transactional `qpe_runs`, completion validation,
  persisted diagnostics, a REST summary and replay-idempotent
  `QPE_RUNNING → ANALYSIS_READY` transition;
- a real Z9598 engineering replay documented in
  `docs/RP011_基础QPE验收记录.md`.

RP-012 now provides:

- a frozen 1.0 DiagnosticBundle with seven analysis-grid layers and four PPI
  layers per actual contributing radar;
- server-side transparent PNG rendering that keeps missing, valid no-rain and
  low-quality states distinct and retains grid/PPI geometry evidence;
- deterministic diagnostic jobs, exact contributor checks, atomic immutable
  publication, transactional persistence and replay-idempotent completion;
- manifest-listed image reads through Go with path, size, PNG, cache and ETag
  controls instead of exposing object storage or parsing arrays in Go/React;
- a responsive React evidence workbench with raw/QC comparison, QI, flags,
  source radar, beam height, QPE, three-state masks and provenance ledger;
- analysis-grid diagnostics now reuse the shared OpenLayers EPSG:4326 raster
  map with manifest-frozen pixel bounds and field-specific legends, while
  non-georeferenced polar PPI remains an explicit raw/QC diagnostic view;
- a real Z9598 engineering replay documented in
  `docs/RP012_React质控与拼图诊断验收记录.md`.

RP-013 now provides:

- deterministic selection of 3–6 committed `RadarAnalysis` frames ending at
  the issue time with an exact five-minute cadence and no silent gap filling;
- a versioned gate profile covering minimum per-frame coverage, sequence QI,
  maximum data age and mandatory upstream operational eligibility;
- a canonical `NowcastInput` v1.2 Zarr preserving missing, valid no-rain and
  low-quality states together with raw/QC/QPE identities and versions;
- transactional input-run/frame provenance, `INPUT_READY`, requested/completed/
  ready events and replay-safe atomic publication;
- real Z9598 negative-gate evidence plus a clearly isolated full-grid synthetic
  vertical acceptance documented in `docs/RP013_NowcastInput验收记录.md`.

RP-015 application delivery now provides:

- an atomic `ApplicationProductBundle` derived only from one committed
  `ForecastOutput` 1.1, with exact source URI/SHA provenance;
- 24 five-minute rain-rate fields, 60/120-minute accumulations, and PNG, COG,
  NetCDF3 classic distribution artifacts on the fixed Fuzhou grid;
- explicit valid/no-rain/missing summaries and a fixed-record point-query index;
- transactional product-build scheduling, three published product identities,
  79 registered assets and durable product-published events;
- product catalog, controlled immutable content reads, point series, area
  statistics and `PUBLISHED` SSE delivery;
- server replay idempotency and event-route regression coverage, documented in
  `docs/RP015_应用产品与API验收记录.md`;
- a responsive React/OpenLayers short-nowcast GIS with a configurable XYZ
  basemap, EPSG:4326 graticule and scale, correctly georeferenced Fuzhou rain
  layer, authoritative lightweight GSHHG coastline and frozen rain legend;
- a 24-frame five-minute playback timeline with previous/next and keyboard
  scrubbing, plus direct PNG/COG/NetCDF delivery links;
- map-click point selection, 24-lead point trend and confidence,
  named-area/bbox statistics, source/product SHA provenance and an explicit
  warning that publication status does not prove forecast skill;
- deployed browser acceptance at 1440, 768, 375 and 320 px with no page-level
  horizontal overflow or browser console errors.

## Active test environment

- Host identity, SSH user, deployment paths and externally reachable endpoints
  are private deployment configuration and are not stored in this repository.
- API-reported runtime label: `rp008-v1.1-0748898-20260824` (the preserved
  deployment label has not been bumped; the code/acceptance commit is
  authoritative)
- RP-009 ancillary runtime: `runtime/ancillary/assets`, accepted 2026-08-25
- The one-time legacy archive location is configured outside the repository.

Phase 1 radar QC and pySTEPS are CPU-first. GPU use is reserved for later
NowcastNet work. Raw-volume retention and expected daily volume must be frozen
before continuous ingest based on the private deployment capacity audit.

When direct registry access is unavailable, continue to use local Linux/amd64
builds and export/import pinned images through an externally configured proxy.
No credentials, private host identities or secrets belong in this document or repository.

The RP-004–RP-015 application code, generated clients, tests and Linux/amd64
binaries pass locally. Private test deployment uses public-key authentication;
passwords are not stored in the repository. On 2026-08-24 RP-007 was deployed in place
without a new source or database backup, followed by RP-008 through RP-012
under the same rule. The Z9598
NAS golden sample decoded to 11 sweeps,
3994 rays and seven canonical fields, then
persisted a complete integrity record with no missing radials, 1.08° maximum
azimuth gap and an `OK` noise channel. The accepted RP-008 pipeline then wrote a
version-isolated QC Zarr, reached `QC_READY`, and recorded mean QI `0.617353`,
278,214 valid gates and 4,036,486 missing gates. Infrastructure, control-plane,
partial-radar degradation, Worker idempotency/failure, real decode, radar
health, radar QC, API and Web smoke tests passed. RP-009 then consumed that
immutable QC volume and was replayed with the immutable
`rp009-hybrid-v1.1`/`hybrid-scan-1.0.1` profile to write RadarGrid v1.3 with
100,701 cells, 3,363 valid cells, 3.34% valid coverage and mean QI 0.31420428,
and reached `RADAR_GRID_READY`.
Its nine reflectivity sweeps retained polar DEM diagnostics; the two
velocity-only cuts were skipped explicitly. The output remains
`operational_eligible=false` because radar readiness, vertical datum, static
clutter and sea/AP gates are unresolved. RP-010 aligned this volume to
`2026-06-15T12:05:00Z` with a `-18 s` offset and published a validated
RadarMosaic with 3,171 valid cells, 97,530 missing cells, 3.15% valid coverage
and mean adjusted QI `0.28919417`. It remains engineering-only because there is
one contributor and that input is not operationally eligible. RP-011 converted
the 3,171 valid cells with the versioned engineering Z–R rule: 329 are valid
no-rain, 2,842 are rain, the mean valid-cell rate is `2.10220828 mm/h`, the
maximum is `31.57593727 mm/h`, and no cell reached the `300 mm/h` cap. It
published a directly validated 142-object RadarAnalysis and reached
`ANALYSIS_READY`, while retaining the same operational degradation reasons.
RP-012 then rendered the accepted analysis plus exact Z9598 QC contributor into
seven grid and four PPI layers: 12 immutable objects, 75,041 bytes and 2,502 ms
runtime. Direct bundle replay, Alpha PNG geometry, controlled image delivery and
idempotent rerun passed without changing the analysis timestamp. RP-013 then
rejected that real engineering analysis before input construction because it
is not operationally eligible. A separately marked three-frame synthetic
acceptance sequence subsequently produced a validated `3 × 201 × 501`
NowcastInput, reached `INPUT_READY`, published its ready event and returned the
same deterministic run/job on replay. RP-014 then introduced the provenance
patch profile `rp013-fixed-5min-v1.1`/`nowcast-input-builder-1.0.1` instead of
mutating that old input. The replacement run retained the same three raw asset
IDs and fed the real `pysteps-lk-1.0.0` worker. It published a validated
`1 × 24 × 201 × 501` ForecastOutput for 5–120 minutes, reached
`BASELINE_READY`, and emitted exactly one baseline-ready event. Its static
acceptance field correctly used the explicit zero-motion fallback; this is
software-path evidence, not a forecast-skill result. Job replay left one job,
one model run and one completion inbox record. RP-015 then generated three
published products and 79 registered assets from that ForecastOutput, including
26 PNG, 26 COG, 26 NetCDF files and one point-query index. Catalog, controlled
content, point, area and SSE endpoints passed; product replay reused the atomic
marker and left the same database cardinalities. The JetStream product subject
and full replay event routing are pinned by regression tests, and all three
product publication outbox events are published. All sixteen long-lived Compose
services are healthy. Desktop 1440 px, tablet 768 px and mobile 375/320 px
browser checks passed; the default short-nowcast view reads the accepted real
product APIs into two OpenLayers canvas layers. Zooming requests a new XYZ tile
level, map clicks update the EPSG:4326 point query, and timeline playback keeps
map, time, statistics and assets synchronized. There is no console error or warning. The
retained PostgreSQL, NATS and MinIO volumes were not deleted.

The local repository is the code source of truth. Deployment and integration
debugging run directly in the server's `rainpulse-nowcast` directory through
the formal launch period. Do not create a source or database backup for every
test release; keep only the one-time legacy archive above unless a backup is
explicitly requested. Persistent PostgreSQL, NATS and MinIO volumes remain part
of the active test environment and must not be deleted during ordinary updates.

## Next acceptance target

Begin RP-016 verification, fault injection and full raw-radar-to-React
operational acceptance against the frozen RP-015 product and UI boundary. Real
RP-013/RP-014 meteorological acceptance still needs at least three consecutive
operational QC→grid→mosaic→QPE cycles with
trackable precipitation. Gauge adjustment remains disabled until representative
gauge observations and quality rules are supplied. Real multi-radar mosaic
acceptance still requires at least two ready radar configurations and
representative synchronized volumes. Static clutter, derived coastline/sea-AP
probability assets, verified vertical datum and a representative
mountain-blockage case remain operational gates.

## Required inputs before operational QC and gridding

The raw sample, RSTM 2.0 format, Z9598 header geometry, field scaling, NAS path
and first basic-QC replay are now known. The following still require business
or radar-maintainer confirmation:

1. Z9598 ground/antenna altitude datum and authoritative station metadata.
2. Radar manufacturer/model, formal meaning of RDA type code 4, calibration
   offsets, calibration interval and maintenance metadata.
3. Real-time delivery SLA, filename timestamp semantics and duplicate/replay
   handling policy.
4. Business-confirmed integrity and QC thresholds for missing cuts/radials,
   field coverage, noise, anomalous values and each RP-008 rule; the current
   values are engineering defaults, not an operational sign-off.
5. Static clutter and derived coastline/sea-AP probability assets; DEM and raw
   coastline sources are accepted and versioned.
6. Representative clear-air, radial-interference, sea/AP, mountain-blockage,
   ordinary-rain, convection and typhoon cases.
7. Later QPE truth: gauge locations, observation interval and quality rules.
8. A second ready radar configuration plus representative volumes synchronized
   to Z9598 for real overlap/blending acceptance.

Until the ready-state metadata, operational ancillary assets, representative
case labels and QC thresholds are verified, the deployed decode/QC chain remains
suitable for replay/integration work only and cannot feed an operational
nowcast.

## Reproducible commands

```bash
make test-radar-config
make test-contracts
make test-grid
make test-ancillary
make test
make lint
make build

# Static ancillary asset preparation on the GPU/runtime server:
make ancillary-plan
ANCILLARY_PROXY=http://127.0.0.1:7897 make ancillary-download
make ancillary-verify

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
make radar-health-smoke
make radar-qc-smoke
make radar-grid-smoke
make test-radar-mosaic
make test-qpe
make test-diagnostics
make test-nowcast-input
make test-pysteps-lk
make test-products
make smoke
```

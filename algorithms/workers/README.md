# Compute workers

RP-005 provides the reusable long-lived Worker runtime in
`rainpulse_algo.worker`. Every profile uses a durable JetStream pull consumer,
strict Pydantic request model, deterministic result IDs, artifact-specific
object-store marker idempotency, temporary output, completion/failure
publishing, structured logs, and a dependency-aware health endpoint.

The registry currently exposes these profiles:

| Profile | Task subject | Committed artifact |
|---|---|---|
| `radar-decode-fmt` | `rainpulse.jobs.requested.radar_decode` | `volume.zarr` |
| `radar-decode-synthetic` | `rainpulse.jobs.requested.radar_decode_synthetic` | `volume.zarr` |
| `radar-qc-basic` | `rainpulse.jobs.requested.radar_qc` | `volume.zarr` |
| `radar-qc-synthetic` | `rainpulse.jobs.requested.radar_qc_synthetic` | `volume.zarr` |
| `radar-grid-synthetic` | `rainpulse.jobs.requested.radar_grid_synthetic` | `grid.zarr` |
| `radar-grid-hybrid` | `rainpulse.jobs.requested.radar_grid` | `grid.zarr` |
| `analysis-mosaic-qi` | `rainpulse.jobs.requested.analysis_mosaic` | `mosaic.zarr` |
| `mosaic-qpe-synthetic` | `rainpulse.jobs.requested.analysis_mosaic_synthetic` | `analysis.zarr` |
| `nowcast-input-synthetic` | `rainpulse.jobs.requested.nowcast_input` | `input.zarr` |

Select one with `RAINPULSE_WORKER_PROFILE`; `simulation` retains the existing
forecast control-plane smoke handler. The `radar-decode-fmt` profile is the
first real compute handler. It accepts configured `file://` sources only below
`RAINPULSE_RADAR_INPUT_ROOTS`, validates CMA RSTM 2.0 metadata and geometry,
and emits a multi-object Zarr v2 `NormalizedRadarVolume`. `radar-qc-basic`
reads that committed artifact, enforces the RP-007 health gate, performs the
versioned RP-008 polar QC modules, and atomically emits a separate
`QCRadarVolume`. The test deployment
mounts only the specific NAS radar directory and mounts it read-only.

`radar-grid-hybrid` reads only an atomically committed `QCRadarVolume`, checks
the frozen radar/grid/DEM/profile versions, computes native-polar cumulative
beam blockage from the accepted GLO-30 tiles, selects the lowest usable DBZH
elevation, and atomically emits `RadarGrid` v1.3 plus polar diagnostics. The
large ancillary directory is mounted read-only. A missing antenna vertical
datum produces an explicit engineering-only artifact; it never becomes
operationally eligible by default.

`analysis-mosaic-qi` reads one committed `RadarGrid` per aligned radar,
verifies the immutable grid and actual volume-end offsets, then selects by
adjusted QI or blends similar candidates in linear Z. It atomically emits an
RP-010 `RadarMosaic`; rain-rate estimation remains a separate RP-011 worker.

The synthetic domain handlers write only small JSON contract fixtures whose
payload explicitly says that no radar array or meteorological algorithm ran.
They do not QC, grid, mosaic, estimate rain rate, or build real NowcastInput
data.

Later tasks replace the remaining synthetic QPE/input executors one at a time while
retaining the same runtime and handler boundary. Production workers remain
separate long-lived processes with distinct durable consumers.

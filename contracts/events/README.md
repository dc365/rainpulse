# Event contracts

The JSON Schemas in this directory are the source of truth for Go ↔ Python
events carried by NATS JetStream. Events use schema version `1.0`, carry only
task metadata and object-store URIs, and never contain meteorological arrays.

Delivery is at least once. Consumers must enforce `job_id` idempotency and ACK
only after validated output is atomically published. Examples in
`contracts/examples` are validated in the contract test suite.

## Domain lifecycle events

- `radar.scan.received.v1` registers one immutable raw asset and radar scan run.
- `radar.decode.requested.v1`, `radar.qc.requested.v1`, and
  `radar.grid.requested.v1` advance the polar-to-Hybrid-Scan workflow.
- `analysis.cycle.opened.v1` opens a fixed UTC analysis time without waiting
  indefinitely for every radar.
- `analysis.mosaic.requested.v1` is retained for the original synthetic
  combined seam. `analysis.mosaic.requested.v2` selects versioned RadarGrid
  inputs and creates the RP-010 `RadarMosaic` only; RP-011 QPE publishes the
  final `RadarAnalysis` separately.
- `analysis.qpe.requested.v1` consumes exactly one committed RadarMosaic and
  publishes the versioned RP-011 `RadarAnalysis` with `RATE_QPE`.
- `nowcast.input.requested.v1` selects 3–6 committed RadarAnalysis frames for
  fixed-step gate evaluation and input construction.
- `nowcast.input.ready.v1` proves the fixed-step input gate was satisfied.
- `forecast.run.requested.v1` starts baseline nowcasting from that committed input.
- `forecast.pysteps_lk.requested.v1` runs the versioned deterministic LK adapter
  together with persistence and whole-field translation diagnostics.
- `forecast.nowcastnet_offline.requested.v1` runs the reviewed official
  NowcastNet backend on a dedicated 9-frame, 10-minute MRMS research input. It
  is isolated from realtime scheduling and product publication.
- `forecast.baseline.ready.v1` proves that the validated 24-step ForecastOutput
  was committed before the forecast run entered `BASELINE_READY`.
- `product.build.requested.v1` consumes exactly one committed ForecastOutput
  and atomically publishes the rain-rate, 0 to 1 hour and 0 to 2 hour product
  suite before the run enters `PUBLISHED`.

`job.requested`, `job.completed`, and `job.failed` remain the common worker
command/result envelope. RP-004 will map the three domain workflow state
machines to those idempotent job records.

## JetStream routing

- Stream: `RAINPULSE_JOBS`
- Task subject: `rainpulse.jobs.requested.<job_type_token>`
- Existing control-plane simulation task: `rainpulse.jobs.requested.model_pysteps_lk`
- Real RP-006 task token: `radar_decode`; synthetic decode uses
  `radar_decode_synthetic` so it cannot compete for real commands.
- Synthetic domain task tokens use `_synthetic` suffixes and cannot compete
  with real `radar_qc`, `radar_grid`, `analysis_mosaic`, `analysis_qpe`, or `nowcast_input`
  commands.
- The real RP-014 task uses `rainpulse.jobs.requested.pysteps_lk`; the legacy
  simulation remains isolated on `rainpulse.jobs.requested.model_pysteps_lk`.
- The RP-015 application-product task uses
  `rainpulse.jobs.requested.product_build`.
- The RP-026 offline-only task uses
  `rainpulse.jobs.requested.nowcastnet_offline`; no production workflow emits
  this subject.
- Completion subject: `rainpulse.jobs.completed`
- Failure subject: `rainpulse.jobs.failed`
- Product publication subject: `rainpulse.products.published`
- Go terminal-result consumer: `rainpulse-orchestrator-results-v2`
- RP-005 simulation Worker consumer: `rainpulse-sim-worker`

Publishers set the event UUID as the JetStream message ID. The Go consumer
records every processed terminal result in `inbox_events` inside the same
database transaction as the job/run update, so redelivery and conflicting
success/failure results are safe.

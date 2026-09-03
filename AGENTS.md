# RainPulse Project Instructions

## Project scope

- This repository is the only active workspace for RainPulse short-term precipitation nowcasting work.
- At the start of a new session, read `PROJECT_MEMORY.md` for the current branch,
  deployment, feature and handoff state. Keep it concise and update it only when
  those facts materially change. Never put credentials or private source data in it.
- Treat `docs/RainPulse_技术架构与实施方案_含雷达质控_v1.1.md` as the implementation and acceptance baseline.
- Product-goal and regional-QC reference materials are private local inputs. They must not be committed.
- `docs/RainPulse_技术架构与实施方案.md` is the superseded v1.0 history only.
- If the active documents disagree, follow the v1.1 Markdown implementation plan until the discrepancy is explicitly resolved.
- Keep unrelated or superseded work outside this repository. The local parent workspace stores old work in `legacy-workspace/`.

## Current delivery boundary

- Start with Phase 0 radar inventory/contracts and repository scaffolding, then deliver the Phase 1A trusted-radar field before Phase 1B nowcasting.
- Phase 1A is: immutable raw radar ingest -> decode on original polar geometry -> polar QC -> DEM blockage and Hybrid Scan -> time-aligned QI mosaic -> basic QPE.
- Phase 1B is: continuous RadarAnalysis -> fixed-step NowcastInput -> pySTEPS-LK -> 24 five-minute forecasts covering 0-120 minutes -> 0-1 h and 0-2 h accumulations -> Go API -> React display -> automatic verification.
- Do not put STEPS ensembles, NowcastNet production inference, numerical-model fusion, online training, or Kubernetes on the Phase 1 critical path.
- Compare pySTEPS-LK with persistence and whole-field translation baselines before declaring skill.

## NowcastNet training track

- Treat `docs/nowcastnet-training/README.md` as the only entry point and source of truth for self-trained NowcastNet work.
- Keep model-training plans, runbooks, and progress records under `docs/nowcastnet-training/`; do not assign new RP numbers to them.
- The training track is offline research. It does not replace the v1.1 Phase 1 critical path, alter accepted RP evidence, or enable realtime publication by itself.
- Preserve the immutable model lineage: the MRMS foundation model is a parent artifact, and later Fujian fine-tuning creates a new child artifact without overwriting the parent.
- Open an independent holdout only after the data split, code revision, configuration, checkpoint, and promotion gates have been frozen and hashed.

## Architecture boundaries

- React calls Go only; it never calls Python workers directly.
- Go owns the control plane: ingest registration, workflow/state, retries, fallback, API, SSE, product catalog, and metadata.
- Python owns the compute plane: meteorological I/O, grids, masks, pySTEPS, model inference, product generation, and verification.
- Go and Python exchange small task/event payloads and object-store URIs. Never transmit full radar arrays through REST, NATS, PostgreSQL, or Redis.
- Python algorithms run as long-lived, idempotent workers. Go must not launch per-task Python processes with `exec`.
- Use OpenAPI and JSON/data schemas as the source of truth; change contracts before implementations.

## Meteorological and reliability invariants

- Preserve the three states: valid no-rain, missing, and low-quality. Never convert missing data to zero rainfall.
- Preserve raw base data unchanged. Perform applicable QC in polar ray/gate space before gridding.
- Keep confirmed non-meteorological QC causes as diagnostics in polar data, but hard-reject them at both Hybrid Scan and mosaic eligibility gates through the shared Phase 1 flag set.
- Every correction retains its cause flag, correction diagnostic, QI component, and algorithm/configuration version.
- Never delete all marine echoes based only on a land/sea mask, amplify severe blockage, or average dBZ directly across radars.
- Use fixed five-minute Phase 1 steps and do not mix incompatible input intervals inside one model adapter.
- All internal timestamps use UTC.
- Every run and product remains traceable to `run_id`, `job_id`, input assets, model version, and config version.
- Assume at-least-once delivery: workers and product publication must be idempotent.
- Write outputs to temporary locations, validate them, then publish atomically.
- A failing enhanced model must never block an available baseline product.
- Verification gates model promotion; no model or fusion weights update online without offline evaluation and review.

## Engineering workflow

- Implement contracts and tests before business logic.
- Keep changes small and tied to one acceptance target.
- Each feature includes code, tests, documentation, and a reproducible command.
- Before handoff, run the relevant Go, Python, React, contract, and Compose checks in proportion to the change.
- Do not claim real-data readiness without a representative sanitized radar/QPE sample and frozen grid/source definitions.

## Deployment target

- Test deployment host, SSH identity and remote paths are configured outside the repository.
- Use public-key authentication for routine access; do not persist account passwords locally.
- The local repository is the source of truth for code changes. Use the remote
  directory for deployment, integration debugging, Compose services and
  hardware-dependent verification.
- Keep the one-time pre-RainPulse archive, but update the active deployment in
  place for subsequent test releases;
  do not create per-release source copies or database dumps unless explicitly
  requested.
- Never store SSH passwords or other secrets in this repository, logs, generated memory, Compose files, or committed environment files.
- Inspect GPU drivers, CUDA/container runtime, disk capacity, ports, and existing services before selecting deployment settings.

## Open decisions to freeze before real-data implementation

- Radar base-data source, format, update cadence, available moments, scan geometry, and delivery mechanism for every radar.
- Target grid, CRS, bounds, resolution, masks, and local display timezone.
- Radar site/hardware/calibration metadata plus DEM, coastline, clutter maps, and their versions.
- Quality-control thresholds, regional/radar profiles, and quality-index formula.
- Exact semantics and units of exceedance-probability products.
- Phase 1 latency/availability thresholds after measuring the actual data path and hardware.

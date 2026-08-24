# RainPulse Project Instructions

## Project scope

- This repository is the only active workspace for RainPulse short-term precipitation nowcasting work.
- Treat `docs/RainPulse_技术架构与实施方案.md` as the implementation and acceptance baseline.
- Treat `docs/短临降水预报技术方案.pptx` as the product goal and algorithm-selection narrative.
- If the two documents disagree, follow the Markdown implementation plan until the discrepancy is explicitly resolved.
- Keep unrelated or superseded work outside this repository. The local parent workspace stores old work in `legacy-workspace/`.

## Current delivery boundary

- Start with Phase 0 contracts and repository scaffolding, then deliver the Phase 1 deterministic minimum loop.
- Phase 1 is: continuous radar/QPE ingest -> standardization -> pySTEPS-LK -> 24 five-minute forecasts covering 0-120 minutes -> 0-1 h and 0-2 h accumulations -> Go API -> React display -> automatic verification.
- Do not put STEPS ensembles, NowcastNet production inference, numerical-model fusion, online training, or Kubernetes on the Phase 1 critical path.
- Compare pySTEPS-LK with persistence and whole-field translation baselines before declaring skill.

## Architecture boundaries

- React calls Go only; it never calls Python workers directly.
- Go owns the control plane: ingest registration, workflow/state, retries, fallback, API, SSE, product catalog, and metadata.
- Python owns the compute plane: meteorological I/O, grids, masks, pySTEPS, model inference, product generation, and verification.
- Go and Python exchange small task/event payloads and object-store URIs. Never transmit full radar arrays through REST, NATS, PostgreSQL, or Redis.
- Python algorithms run as long-lived, idempotent workers. Go must not launch per-task Python processes with `exec`.
- Use OpenAPI and JSON/data schemas as the source of truth; change contracts before implementations.

## Meteorological and reliability invariants

- Preserve the three states: valid no-rain, missing, and low-quality. Never convert missing data to zero rainfall.
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

- Test deployments may use the GPU server at `private-test-host` with SSH user `<ssh-user>`.
- Remote project directory: `<remote-project-dir>`.
- The remote directory is a test/deployment target, not the source of truth. Develop and verify locally, then deploy explicit artifacts or revisions.
- Preserve any pre-existing remote contents under a sibling `legacy/` archive before deploying RainPulse.
- Never store SSH passwords or other secrets in this repository, logs, generated memory, Compose files, or committed environment files.
- Inspect GPU drivers, CUDA/container runtime, disk capacity, ports, and existing services before selecting deployment settings.

## Open decisions to freeze before real-data implementation

- Radar/QPE source, format, update cadence, available variables, and delivery mechanism.
- Target grid, CRS, bounds, resolution, masks, and local display timezone.
- Quality-control algorithms, thresholds, and quality-index definition.
- Exact semantics and units of exceedance-probability products.
- Phase 1 latency/availability thresholds after measuring the actual data path and hardware.

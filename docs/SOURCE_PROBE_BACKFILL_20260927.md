# Historical source-value index backfill

## Release

- Local main is the deployment source. Diagnostic profile `qc-full-range-source-probe-v16` / renderer `radar-diagnostic-renderer-2.0.3-probe` preserves the live meteorological v2 palette.
- Host 105 uses `deploy/docker-compose.diagnostic-source-probe-20260927.yaml` after the existing complete Compose chain. Its profile is mounted outside the read-only config directory.
- `scripts/backfill_source_probe_diagnostics.py` enqueues 106 available analysis cycles for UTC 2026-08-28 through the public orchestrator maintenance command. Requests are idempotent and output paths are versioned.
- Existing raw/QC assets and previous diagnostics are retained. The planner resolves the currently registered QC inputs for each scan; these can differ from an older diagnostic job's frozen inputs. This is a new diagnostic generation, not a promise of identical historical image pixels.

## Acceptance and transport correction

- Analysis `0580e801-78ab-5cdb-9cf8-904a73d5b41e` succeeded as job `f0c55d40-2dd6-57d7-8be7-8f43db03ff36`. Real S source probes returned numeric reflectivity and distinguished missing pixels.
- Four-station completion events exceeded the broker payload limit because optional numeric-tile directories were included. Runtime transport now removes only each diagnostic layer's optional `probe` directory from the event copy. Immutable manifests and completion markers retain it; event identities and asset checksums remain intact.
- Regression covers both fresh publication and replay of a committed oversized marker. Targeted runtime suite: 11 passed.
- Live worker runtime predates other local runtime changes. Deployment applies only the transport insertion to the existing automatic/managed diagnostic images. Tags: `rainpulse-cpu-worker:transport-9f00114-analysis-diagnostics-worker` and `rainpulse-cpu-worker:transport-9f00114-ops-diagnostics-worker`; override `.build/sx-probes-b91b81b/transport.compose.json` follows the full chain.
- Already accepted failure events remain authoritative under the control-plane terminal inbox constraint. Replaying the genuine completion does not replace them. These four cycles require a new versioned diagnostic request; never delete inbox records or rewrite job statuses to simulate recovery.

## Backfill operation

Four temporary automatic workers process the queue; replicas 2–4 are capped at 16 GiB and 2 CPUs. `scripts/finish_source_probe_backfill.py` records progress and returns to one replica when diagnostics are idle. Its saved Compose command includes the transport override. A settled batch with failures is explicitly not reported as complete.

## X metadata boundary

The historical X tree contains 24 directories and 5,466 compressed files. First/second/last samples per directory provide stable header identities and candidate geometry, but header naming aliases and frequency representations differ across formats. Headers alone do not establish trusted height datum, calibration or attenuation corrections. Keep trusted fusion eligibility unchanged until evidence is supplied. An explicitly labeled uncalibrated experimental composite is a separate product decision, not a verified configuration toggle.

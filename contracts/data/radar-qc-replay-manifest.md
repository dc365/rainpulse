# Radar QC Replay Manifest contract

`RadarQCReplayManifest` freezes the exact normalized radar inputs and optional
temporal/cross-radar context used by `radial_audit.py --manifest`. It is the
scientific replay boundary for offline QC evidence validation and must remain
deterministic across retries.

## Top-level fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | string | yes | Current version is `1.0`. |
| `mode` | string | yes | `replay_manifest` for a frozen offline input set; `catalog` for a generated discovery manifest. |
| `radar_ids` | array[string] | yes | Ordered radar list covered by the manifest. |
| `start_time` | string | yes | Inclusive UTC ISO-8601 bound for the frozen selection window. |
| `end_time` | string | yes | Exclusive UTC ISO-8601 bound for the frozen selection window. |
| `snapshot_time` | string | no | Present for paged catalog discovery; copied from `/radar-scans` snapshot pagination. |
| `expected_scan_ids` | array[string] | yes | Unique ordered scan IDs that define the expected audit workload. |
| `scans` | array[object] | yes | Frozen ordered scan inputs. Each entry must match `expected_scan_ids` exactly. |

## Scan entry fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `scan_id` | string | yes | Immutable radar scan UUID. |
| `radar_id` | string | yes | Station identifier. |
| `volume_start_time` | string | yes | Inclusive UTC ISO-8601 observation start. |
| `volume_end_time` | string | yes | UTC ISO-8601 observation end used for ordering. |
| `normalized_uri` | string | yes | Primary normalized radar artifact URI. `input_uri` may be accepted as a backward-compatible alias when loading. |
| `temporal_context` | array[object] | no | Ordered same-radar evidence selected online and frozen for replay. |
| `cross_radar_context` | array[object] | no | Ordered neighbouring-radar evidence selected online and frozen for replay. |

## Context entry fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `radar_id` | string | yes | Expected radar identity for the context artifact. |
| `input_uri` | string | yes | Frozen normalized artifact URI for the context input. |

## Invariants

- `expected_scan_ids` must be unique.
- `scans` must match `expected_scan_ids` exactly after normalization.
- Ordering is deterministic by `(volume_end_time, scan_id)` for replay.
- The manifest is the only discovery source in `--manifest` mode; audit success
  or failure must not change `expected_scan_ids`.
- Resume fingerprints must incorporate the canonical manifest SHA together with
  QC profile, configuration and code fingerprints before prior successes are reused.
## Content verification and recovery

Every scan and context entry requires `artifact_sha256`, the canonical multi-object
SHA-256 computed by `artifact_sha256` over the normalized bundle. Catalog discovery
resolves these digests before executing. A URI-only legacy manifest is rejected;
export a new frozen manifest from catalog mode. The report's `source_manifest` is
the replayable input document. Manifest mode verifies supplied digests and never
silently refreshes them. Unavailable inputs remain failed evidence, and a completed
record cannot resume while any of its frozen inputs cannot be verified.

The run fingerprint includes audit mode, all Python computation-module sources,
QC/flag profiles, and configured runtime geometry/ancillary/physics asset contents.
Switching saturation/evidence or changing an algorithm dependency invalidates resume.

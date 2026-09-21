# Near-joint v1 — existing NP activation and CF extension contract

Status: experimental, `operational_eligible=false`. Existing pipeline_version is
unchanged (a coordinated Literal); profile_version plus hashed configuration
identify each new generation. Current deployment/configurations are not rewritten.

## Configuration and owners

`nonprecip_review.near_enabled=true` activates existing `near_clutter.candidates`.
`near_nonmet` is explicitly allowed and NP owns quantitative quarantine as before.
Opt-in `near_reliability` excludes unverified |ZDR|>=7.5 tails from this precise
numeric path, including neighbour samples. Tail is not absent-at-random and not a
confirmed manufacturer censor code. All old unopted profiles serialize identically.

`volume_review.clutter_fusion.near_revision` adds partial polarimetry, raw causal
context and terrain reliability to CF's existing disposition, not a new independent
OR-of-image-masks stage. Explicit settings are in `NearRevisionConfig`.

## Immutable and separate meanings

RAW values and VALID never change; missing, valid below-no-rain, and low quality
are separate. No intensity smoothing, filling, dBZ subtraction, or gap growth.
No changes to RDR/source-family/radial code. No new confirmed clutter causes.

CF_NR partial/temporal paths use their own target SNR/RHOHV/PHIDP and actual adjacent
phase pairs, >=6 real neighbourhood samples and circular increment scatter. ZDR
is not required; high reflectivity texture is not required. These are correlated
expressions of **one** polarization family, not independent votes. Their net new
actions are **CR-only**, never additional QPE/trust/quality/flag changes. Existing
CF and NP quantitative actions remain exactly separately attributable.

Raw past support requires same station AND processing identity, different scan,
all donor rays strictly before target, bounded time/geometry/measurement changes,
most recent measured comparable support. It never uses old QC as truth or replaces
an incompatible recent sample with an older match. No processing identity => no
operational temporal evidence. A redacted test archive cannot establish identity.

## Context transport

`prepare_open_source_inputs` reuses validated frozen raw temporal roots, versioned
terrain and beam geometry. It returns an in-process `near_clutter_context` through
`apply_basic_qc` -> `run_open_source_qc` -> VOR -> CF. It does NOT put radar arrays
into events/HTTP/database, scan for latest files, or issue new background queries.
Identity, cutoff and source hashes enter the existing context fingerprint. Budget
overflow clears the new extension for the **whole volume**, retaining parent CF.

## Terrain is reliability, not a clutter species

Reuse existing RainPulse beam centre/radius/circular-partial-blockage primitives.
DEM sample locations use actual ground arc, not slant range as horizontal distance.
Need versioned terrain identity and antenna `verified_egm2008`, same radar/processing
configuration. Unverified 1985 national height datum cannot be relabelled EGM2008.
Missing upstream DEM makes downstream cumulative support unknown.

PBB>=0.1 is a local interception prior. CBB>0.7 can withhold a contribution from CR
under explicit dem_policy. It does not label all downstream gates as ground clutter,
does not invent zeros, and does not change QPE. Existing Hybrid remains its own
consumer; no automatic propagation of low-threshold BEAM_BLOCKED into hard rejects.
First release samples the requested 2–75 km domain; it is not an all-range DEM rewrite.

## Fields / provenance

All CF_NR numeric fields have original ray/gate matrix shape. Masks are uint8 0/1;
indices int32 with -1 unknown. CF_NR_TEMPORAL_SOURCE indexes the per-target-sweep
near_revision.temporal.sources receipt. Its ray/gate values refer to ORIGINAL donor
acquisition order, not sorted rows. AGE_S and matching errors are stored.

CF_BG_STATE/REASON and departure/mapping errors copy the real episode model output;
background absence remains separately reported and cannot imply clear weather.
CF_NR_STATE codes: 0 missing; 1 outside; 2 current nonmet risk; 3 causal risk;
4 severe obstruction; 5 protected; 6 insufficient. CF_NR_REASON bits:
1 current partial-polar; 2 past support; 4 background match; 8 background unavailable;
16 local terrain interception; 32 severe CBB; 64 weather protected; 128 feature missing.

CF_NR_CR_WITHHELD_MASK is net new versus old CF. Its disjoint cause partition:
PARTIAL, TEMPORAL, DEM; old CF has first attribution priority. All changes also enter
CF_CR_WITHHELD_MASK. CF_BEFORE arrays retain exact pre-CF values, and recursive
validation first runs unchanged parent validators. A candidate is not a deletion.

CR validates masks/configuration before taking all-contributor maxima. New risk
arrays CR_NEAR_JOINT_NONMET, CR_NEAR_JOINT_WITHHELD, CR_TERRAIN_UNRELIABLE retain actual
raw values on their measured masks. WINNER_NEAR_JOINT_STATE/REASON and
WINNER_CUMULATIVE_BLOCKAGE accompany existing winner/runner-up pointers. Audit risk
maps can be nonempty without changing trusted values. Any withheld winner is an error.

## Acceptance

No RAW changes, no restored exclusions, zero ineligible winners, exact parent state
restoration; new partial/time/DEM QPE changes zero. Report NP incremental quarantine,
new CF CR-only exclusion and terrain reliability coverage separately. Require full
parent/child Worker+Zarr+four-station CR replays and independent weather labels before
any operational promotion. Blue area reduction is NOT a detection accuracy metric.

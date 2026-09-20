# Receiver finite-state reference extension v1

Base repository: dc365/rainpulse, commit `6ea9508b509a1927edd2e4ff3403379d598dbd36`.
Parent: receiver-domain-20260921-v1. New optional configuration:
`volume_review.receiver_domain.segment_reference`.
Version: `receiver-segment-reference-v1`. Default mode: `audit`.
Omitted configuration preserves the parent's serialized configuration/digest,
all previous output fields and actions. No scheduler/default deployment changes.

## Reference semantics

The existing whole-ray fit is attempted first, unchanged. ONLY its failed folds
may call the finite-state fallback. The fallback's target domain is the parent's
original measured DBZH domain, including its original range/no-rain boundaries.
Reference minimum range is a SEPARATE explicit physical setting (10 km by default).

State discovery uses original available SNR outside the target block and all
parent guard blocks. States below the parent's minimum SNR are not source models,
but are NOT declared missing, zero or clear-air observations. Split strong-state
populations at fixed gaps (default 6 dB), then reject insufficient or nonstationary
populations; never carve an arbitrary continuous distribution into many tiny bins.
All discovered states use train samples only, never target-dependent nearest-state
selection. At most three states, plus explicit trial budget.

Every state reuses parent fit_fold's measurement checks. Finite references require
>=3 retained blocks, >=40 km span, >=12 km actual measured support by default,
plus all unchanged pair/polar sample, model-error and stationarity constraints.
Alternating retained reference blocks additionally predict each other using
independently fitted DBZH/SNR processing relations. This is a held-out stability
check, NOT a second independent physical source observation.

Targets must match exactly one train-defined state, be <=50 km from an ACTUAL
retained paired reference sample, and have both measured SNR shoulders with the
FULL parent angular contrast. Missing shoulders cannot qualify the new action.
The actual raw target polar moments still decide full versus partial compatibility;
any available polar contradiction or guarded numeric tail remains a veto. No gap
is filled, no new classified gate becomes a training anchor, no recursive growth.

## Optional gate fields

Existing RDR_* fields retain their definitions. When configured, add:

| Field | Type | Meaning |
|---|---|---|
| RDR_SEGMENT_REFERENCE_MASK | uint8 | This measured target was evaluated using a finite-state reference. |
| RDR_SEGMENT_MATCH_COUNT | uint8 | Number of compatible trained states; >1 forbids full/partial acceptance. |
| RDR_SEGMENT_AMBIGUOUS_MASK | uint8 | Exactly `reference_mask & (match_count > 1)`. |
| RDR_SEGMENT_SIDE_MEASURED_MASK | uint8 | Both selected target shoulders have original SNR observations. |
| RDR_SEGMENT_REFERENCE_DISTANCE_M | float32 | Distance in metres to nearest actual paired reference gate; NaN outside route. |

Masks are observed-only. Model IDs reference existing receiver_domain.json model
records. New records add train-pool digest, state partition bounds, physical
reference limits and two cross-prediction records. Ray and shoulder IDs are still
converted to original acquisition order by the existing adapter.

## Modes and invariants

- Nested audit keeps ALL existing parent-product fields unchanged by this route;
  existing long-source actions remain enabled exactly as selected in the parent.
- Nested experiment allows only new source-qualified gates through existing RDR
  parent modes. Parent `cr_only` or `quarantine`, external/unknown protections,
  QPE eligibility, before-state preservation and derivative invalidation remain.
- Nested partial_policy defaults to diagnostic_only. Optional cr_withhold only
  withholds compatible partial gates from CR; never adds QPE quarantine for them.
  It does not change the parent's policy for pre-existing long-source partials.
- Preserve all raw/valid/no-rain fields; no old ineligible gate is revived.
- Keep confirmed contamination count zero; this is an uncalibrated engineering
  candidate, never independent meteorological truth or automatic promotion.
- On resource overflow, the existing adapter discards the entire RDR enhancement
  for all sweeps and exposes RESOURCE_ABSTAINED; no partial action is retained.
- Extend strict serialized validation; restore and validate the unchanged
  pre-RDR VOR/NMR/NP chain first, then check the exact RDR delta. No old validator
  is weakened. CR all-contributor selection and winner reconstruction are reused.

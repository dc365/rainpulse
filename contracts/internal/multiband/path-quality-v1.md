# X path quality / S-X quality-height-v2 contract

Version: `x-path-quality-20260929-v1` and `sx-quality-height-20260929-v2`.
Candidate-only, additive to existing multiband contracts. No change to QPE eligibility.

## Source declarations (not manufactured by the adapter)

Raw fields remain unchanged. All masks have native (ray,gate) shape and values 0/1.
New optional source fields are `CLEAR_PATH_MASK`, `PATH_ANCHOR_VALID_MASK`,
`PATH_ANCHOR_PIA_DB`, `ATTENUATION_NEGLIGIBLE_MASK`, `RADOME_VALID_MASK`.
The corresponding metadata declarations and their provenance requirements are
specified in `docs/SX_FUSION_PATH_20260929.md`. A syntactically valid SHA is not
independent proof of a physical condition; the registered producer owns this assertion.

## Derived X fields

| Field | Type / units | Meaning |
|---|---|---|
| DBZH_ATTENUATION_CORRECTED | float32 dBZ, NaN unavailable | Path-qualified value; wet radome/calibration remain separate |
| PIA_DB | float32 dB, NaN unknown | Two-way path loss including known loss at the start, excluding radome loss |
| AH_DB_PER_KM | float32 dB/km, NaN unknown | One-way specific attenuation (Z–Φ) |
| KDP_EST | float32 degree/km, NaN unknown | Diagnostic derivative, not automatic QPE admission |
| PATH_VALID_MASK | uint8 0/1 | Path qualification only |
| PATH_STATE | uint8 | 0 unknown,1 not-required,2 upstream,3 linear,4 ZPhi,5 verified-clear |
| PATH_REASON | uint16 bits | Stable reasons from `PathReason`; not a probability |
| RADOME_QUALIFIED_MASK | uint8 0/1 | Independent declared negligible/corrected radome condition |
| CALIBRATION_QUALIFIED_MASK | uint8 0/1 | Matching registered verified calibration identity |

Reasons: 1 initial-loss-unknown,2 phase-unavailable,4 non-liquid/contaminated,
8 phase-span-small,16 phase-jump/noise,32 correction-limit,64 radome-unverified,
128 short-segment,256 path-broken,512 method-not-configured,1024 invalid-input.

`DBZH_QC`/display may retain a raw uncertain value for diagnostics. Neither is a
substitute for path, radome, calibration and final CR admission masks. Reject and
withheld XQC evidence cannot be undone by attenuation correction or visualization.

## Fusion v2

Grid `method=quality_height_v2` is opt-in. The selected input must still be causal,
geometry-qualified and within its own station's maximum observation age.
Strict derived views contain `FUSION_ELIGIBLE_MASK`, `FUSION_UNRESOLVED_MASK`,
`FUSION_UNCERTAIN_DBZH` and `FUSION_INPUT_REASON` without mutating source arrays.

Output `AVAILABLE_BAND_BITS` and `QUALIFIED_BAND_BITS`: S=1,X=2,both=3. They are
ORs over represented height levels, not weights or same-height overlap. A source
with explicit attenuation censoring can contribute uncertainty without a finite Z.
`WINNER_BAND` describes the column's selected echo (0 for no echo winner).
`S_SELECTED_WITH_X_AVAILABLE_MASK` and its converse are descriptive facts, not a
counterfactual classification of why another radar lost. Source records contain
`quality_receipt`; numerical assets keep the exact source/ray/gate/height/age.

Input reasons: 1 path-unqualified,2 radome-unqualified,4 calibration-unqualified,
8 QC-withheld,16 invalid-measurement,32 missing-path-contract.

S-only and X-only products are valid candidate outcomes. No eligible source means
missing/uncertain, not zero rain. `OBSERVED_MASK` continues to mean at least one
qualified sampled height, not complete vertical coverage. An unresolved column
cannot be labeled no-echo merely because another height has a clear sample.

Network schema and Python additionally validate explicit alpha/beta, frequency
bounds, coefficient identities and resource bounds. The numeric implementation is
`zphi_segment` (normalized trapezoidal path integral); not a Py-ART wrapper and not
claimed bitwise-equivalent to Py-ART's differing pre-processing/integration choices.

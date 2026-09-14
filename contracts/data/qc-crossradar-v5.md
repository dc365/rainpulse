# Cross-radar capability and measurement-signature QC v5

Candidate only. Pipeline `qc-opensource-5.0.0`, decision `crossradar-v5`, flags v2.
V1--V4 profiles and semantic hashes MUST remain unchanged. V5 first evaluates the
unchanged V4, then adds independently diagnosed measurement actions. No SWAN
implementation, no guessed RDD, no new training, and no raw-value correction.

## Inputs and identity

Use native sweep geometry and actual moment availability. Capability is per gate:
0 unobserved, 1 reflectivity only, 2 partial/unreliable polarimetric support,
3 at least two raw polarimetric moments AND a measured reliable SNR. Missing SNR
is not low SNR or reliable SNR. Same-shaped arrays are still checked by the native
adapter. No station IDs or fixed azimuth deletion lists are algorithm inputs.

A known reflectivity output ceiling is optional verified metadata, keyed by
radar_config_version and native sweep name, with a SHA256 source receipt. This receipt is a frozen human-verified declaration,
not proof automatically obtained by the Worker; the source document must be audited before
registering it. The default list is empty. In its
absence a numeric plateau is only an unverified hypothesis. Display palette
limits and receiver compression are NOT reflectivity-encoding ceilings.

## Computation and actions

A long-range expert uses measured distance, bounded gaps, observed length, range
ratio, near/far change, robust distance-compensated residual, angular extent,
radial aspect. It does not infer clear air from missing flank measurements. For a verified output ceiling it fits
uncensored samples and checks lower-bound inequalities for censored observations.
An unverified plateau may create a candidate, but cannot authorize confirmation.
A flat high-valued patch or high dBZ alone never qualifies. Missing gates never
receive object membership; observed incompatible patches stop linkage.

A separate v5 literature path associates candidates across bounded gaps without
changing frozen AFL formulas or V4 decisions. All original masks remain auditable.
Agreement between two DBZH-derived classifiers is not independent evidence.

Keep four actions: KEEP/DOWNWEIGHT/REJECT/MISSING. High-risk uncertain measurements
are DOWNWEIGHT + RFI_QUARANTINE_MASK and excluded from QPE and phase processing;
this is not a confirmed detection. Confirmations need reliable polarimetric
corroboration, or an explicitly enabled, receipt-bound experimental single-field
policy. High RHOHV is not universal immunity. Actual corroborated weather can
conflict with strong measurement evidence; preserve the conflict, never restore
an already rejected baseline measurement.

## Output

`V5_CAPABILITY_CODE` uint8; `V5_DECISION_REASON` uint32 bit flags;
`V5_RANGE_MODEL_CODE` uint8: 0 none, 1 log-distance, 2 verified ceiling,
3 unverified numeric plateau. `V5_RANGE_OBJECT_ID` uint32.
All binary arrays ending `_MASK` are uint8 on original ray/gate geometry.
Fit residual/growth/span float32 is NaN off its available domain.
`V5_BASELINE_REJECT_MASK`, `V5_BASELINE_QUARANTINE_MASK` capture exact V4 decisions.
`V5_CONFIRMED_ADDITION_MASK` and `V5_QUARANTINED_ADDITION_MASK` explain each change.
The v4 paper validator is checked against this captured baseline, then the v5
validator checks the final action/trust/eligibility composition. Old v4 reports
remain interpretable and old parameter hashes remain identical.

The per-sweep summary records capability by distance band, candidate/structure/
reliability/conflict/final-action counts, rejection versus quarantine separately,
and provenance of verified ceiling metadata. Counts are engineering diagnostics,
not skill. Machine-readable group comparisons must keep fixed label denominators,
reject duplicate physical scans/input hashes and split leakage, report quarantine
loss and weather unavailability, and make any failed or insufficient required
station/group prevent a network PASS. No automatic operational promotion.

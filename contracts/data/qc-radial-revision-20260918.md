# Radial revision 20260918: opt-in extension contract

Base: `547ead93f0a2849893f008dafcbbf78f1f2a483d`, QC 7.3.6 / review-20260917-v1.
The new nested `generalization.broad_source.source_review.radial_revision` is
absent by default. Absent configuration preserves historical parameter hashes,
arrays, decisions and summary semantics. A distinct child profile is mandatory.

## Ordered stages

1. Raw-support topology nominates **observed** gates without interpreting absent
   flanks as clear air. Only its intersection with the existing target-held-out
   source match may propose quarantine.
2. Fixed physical target/guard blocks are excluded before ALL range calibration,
   state discovery and reference fitting. SNR states are discovered from training
   data only. Disjoint training block groups must agree on the source measurement.
   A target must match exactly one reference state. Strong coherent matches may
   propose experimental quarantine. Weak/incomplete-pol hypotheses remain
   diagnostic-only; they never inherit the strong state's action. This is not a
   second noisy classifier nor an IQ/SQI algorithm or a calibrated probability.
3. Wider non-uniform bundles use actual measured outside flanks (not nearest
   polluted neighbours) and independent physical windows. Fragment identity links
   are bounded from the first raw segment, never fill gaps, and cannot cross a
   protected gate or an observed noncandidate interval. Identities confer no
   qualification; every gate still needs its own source match.

## Safety and provenance

`RV2_*_MASK` are uint8 finite binary, false outside native DBZH observation and
valid geometry. Missing raw codes remain unknown. Coordinate gaps, weather,
external conflict, numerical-plateau ambiguity and local power enhancement are
barriers. No source comes from final QC/old isolation masks. No DBZH correction,
new confirmed flag, data imputation or eligibility restoration is permitted.

`SRC_REVIEW_QUALIFIED_MASK` is the union of the unchanged legacy qualification and
`RV2_ACTION_PROPOSAL_MASK`. The latter is nonzero only in explicit revision
experiment mode; the existing outer source and broad-source modes still control
publication. `RV2_QUALIFIED_MASK` records source-supported hypotheses in either
mode. Existing generalization quarantine/QI/budget logic owns final disposition.

Weak evidence is never auto-censored in this version. Resource exhaustion
abstains for the complete new extension, not a silently selected subset; the
legacy baseline remains available. Invalid inputs/configuration raise errors.

Per-gate diagnostics include candidate families, scale bits, source family,
reference fold/model identity, reference span, residual, holdout conflict and
barriers. Fold definitions and reference models are deterministic from raw/config;
a compact SHA-256 and count are included in runtime summaries. The replay tool
exports the full model records separately, never into task/event messages.

`operational_eligible` remains false. Development replay is not independent
weather acceptance. The package does not change planner defaults or deploy.

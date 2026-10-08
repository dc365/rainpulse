# Paired range calibration with ray offsets

The range term describes paired DBZH/SNR processing, not contamination truth.
Do not use it to override a weather barrier or authorize morphology alone.

Input is original polar DBZH/SNR, a finite paired eligibility mask and a frozen
reference range mask. Targets, guard windows and protected observations must be
excluded before fitting. Missing observations remain missing.

Each accepted ray has at least 100 paired reference gates spanning 200 km.
Fit one shared slope with an independent intercept for each ray. Short-lived
range-dependent changes in the contributing ray population cannot create a
shared intercept. Both disjoint ray parity groups require at least five rays.
Physical 50 km blocks alternate between fit and validation, in both directions.
Validation uses the intercept fitted on the other blocks, never its own median.
Every participating ray in each parity/fold must pass P90 <= 0.5 dB and the
shared slope must be in [0, 0.03] dB/km. Slopes
must agree within 0.002 dB/km across folds and parities. Insufficient support,
contradiction or nonlinear response explicitly abstains, with a zero fallback.

Diagnostics bind the reference arrays, fit/validation ray counts, block sets,
slopes and errors. No target values enter source membership or calibration.
This correction does not broaden power, phase, SNR, ZDR or RHOHV target bounds.

## Reference processing cohorts (opt-in candidate, 2026-10-08)

`component_range_calibration` defaults false and requires shared range calibration.
Rather than applying a whole-volume calibration to every ray, discover processing
cohorts from eligible reference samples only. Each reference ray must independently
pass the same two-direction 50 km fit/validation blocks, original slope bounds,
0.5 dB P90 and 0.002 dB/km agreement. Intercepts are never fitted on held blocks.
Group supported rays into fixed 0.002 dB/km slope bins; do not recursively merge
bins. Each cohort then passes the unchanged strict shared calibrator, including
at least five rays in each original ray parity and every ray's validation test.

Calibration membership selection uses reference validation; it is not an
independent holdout, contamination evidence, or weather accuracy estimate.
Successful whole-group calibration is preserved exactly. On failure, unsupported
rays retain the existing zero-term path and cannot inherit another cohort's
coefficient. Cohort membership only supplies a processing relation, not an action. Every target and guard remains excluded
from both membership and fits. The original weather/conflict/target-polar barriers
remain required. Diagnostics retain cohort membership, rejected rays, reference
SHA and strict validations. Failed cohorts are not treated as a measured zero slope.
Existing scalar calibration and frozen v9 outputs remain unchanged unless opted in.
Promotion requires real 10:42 regression and 14:06/control replays and normal
publication verification; old QC masks are comparisons, never truth labels.

When any whole-group fold fails, retain the current scalar path's validated
actions recomputed from the same immutable RAW and current weather/conflict
context. This is not a union with stored old QC labels. Retain original cause,
fold and residual diagnostics for those gates; report their count.

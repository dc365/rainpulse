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

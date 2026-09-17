# QC 7.3.2: experimental distance-conditioned polar reference

Opt-in `broad_source.distance_polar_reference`; disabled preserves existing identities.
The source-power and SNR hypotheses retain target range-block plus adjacent-block
holdout. Polar calibration instead uses other connected, source-qualified rays in
that same 50 km range band; the target ray and ±1.5 degree angular guard never
supply calibration observations. These are distinct holdouts, not a claim that
all rays at target range are excluded from the polar reference.

At least four donor rays, each with 100 valid measured samples, must support the
reference. Two disjoint ray groups must agree on the predictive envelope. Donors
exclude externally protected/conflicting gates, missing values and numeric plateaus;
source-power agreement is necessary. Circular phase is centred using each donor's
held-out far-range fit. No target-based width selection or missing-gap filling.
Insufficient/inconsistent evidence falls back to the existing model, with status.
The extra candidate can only be quarantined, never labelled confirmed RFI. Existing
weather, enhancement, geometry, missing-value and plateau vetoes remain in force.
Fold diagnostics retain donors, bounds and calibration status. This is an engineering
candidate, not meteorological validation; production promotion requires real replay.

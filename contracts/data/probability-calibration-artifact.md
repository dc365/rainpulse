# ProbabilityCalibrationArtifact 1.0

`ProbabilityCalibrationArtifact` is an immutable, offline-fitted mapping from
raw STEPS member-relative event frequency to calibrated exceedance probability.
It does not alter ensemble members, rain-rate quantiles, QPE, or deterministic
fallback products.

## Identity and provenance

Every artifact records:

- the RP-025 profile, artifact contract, source forecast contract and strict
  `greater_than` event operator;
- the exact model/profile, grid, thresholds and lead bands used for fitting;
- a training namespace, source-manifest SHA-256, case IDs, case/issue/sample
  counts and the `calibration_training` role;
- a disjoint reserved `calibration_validation` namespace and case IDs that
  were untouched when the artifact was fitted;
- one monotone isotonic curve for every lead-band/threshold combination,
  including event/non-event counts and raw/calibrated training Brier scores.

Training and validation namespaces and case IDs must be disjoint. A forecast,
verification result, or skill field from the reserved validation split must
never participate in fitting or curve selection.

## Mapping semantics

The v1 method is weighted pool-adjacent-violators isotonic regression over the
finite raw probabilities. Application uses linear interpolation between fitted
knots and clips only outside the fitted input range. Missing cells remain
missing; valid probabilities of zero remain numeric zero inputs.

Each threshold is fitted separately, but the applied suite must enforce the
physical exceedance ordering
`P(R > 1) >= P(R > 5) >= ... >= P(R > 50)` at every valid cell. An incoherent
raw probability suite is rejected. The calibrated suite uses a cumulative
minimum across increasing thresholds and records that coherence projection in
the artifact method metadata.

## Fail-closed boundary

Fitting is rejected when samples, cases, issues, events, non-events or unique
raw probability values do not meet the profile minimums. Application is
rejected for an unknown lead band, threshold, method, source semantics, invalid
probability, incomplete curve suite or overlapping evaluation data.

RP-025 is a data-independent foundation only:

- artifact fitting, shadow application and calibrated product publication are
  disabled in the checked-in profile;
- every artifact has `operational_eligible=false` and
  `publication_enabled=false`;
- raw probabilities remain the traceable source and deterministic LK remains
  the available operational fallback;
- enabling calibrated publication requires representative Fujian QC/QPE
  training data and an untouched Fujian validation acceptance.

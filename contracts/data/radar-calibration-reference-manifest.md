# Radar Calibration Reference Manifest 1.0

`RadarCalibrationReferenceManifest` freezes the offline C2-P3 split boundary
for attenuation coefficient fitting and shadow-only evaluation. It records
which independent references supported coefficient fitting and which references
were reserved for untouched validation.

## Identity and provenance

Every manifest records:

- the calibration shadow profile version and schema version;
- the radar band, generation timestamp, immutable reference URI, and source
  SHA-256 for every supporting dataset;
- one role per reference entry: `coefficient_fitting` or
  `independent_validation`;
- the participating radar identifiers, process identifiers, case identifiers,
  and truth-kind labels used to qualify each reference.

## Split semantics

- Fitting and validation entries must be disjoint by process_id and case_id.
- A weather process must not both fit coefficients and serve as independent truth.
- Validation references remain untouched while coefficients are selected or
  frozen.
- Relative-bias overlap evidence can support readiness review, but it must not
  replace an independent truth reference.

## Fail-closed boundary

- Missing process IDs, case IDs, truth labels, or source digests invalidate the
  manifest.
- Unsupported roles or truth kinds invalidate the manifest.
- Worker integration remains disabled until the disjoint split, representative
  Fujian evidence, and acceptance criteria are frozen.
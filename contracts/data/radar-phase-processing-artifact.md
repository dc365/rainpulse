# Radar Phase Processing Artifact 1.0

`RadarPhaseProcessingArtifact` is an immutable offline artifact for C1 shadow
validation. It records how raw `PHIDP` was normalized, segmented, system-phase
adjusted and locally fitted into `KDP` and uncertainty without changing the
operational QC worker.

## Identity and provenance

Every artifact records:

- the frozen C1 shadow profile version and artifact contract version;
- the source normalized radar-volume contract version and immutable source URI;
- the radar identifier, scan identifier, input phase unit and source manifest
  SHA-256;
- per-sweep summaries of segment counts, system-phase availability, available
  gate counts and failure reasons;
- `operational_eligible=false` because this artifact is offline evidence, not
  a release gate by itself.

## Processing semantics

The C1 shadow pipeline is fixed to:

1. trusted finite gate selection;
2. contiguous valid-segment partitioning;
3. system-phase estimation from the first valid gates of a segment, unless an
   explicit external system phase is provided;
4. per-segment PHIDP unwrap only within that segment;
5. robust local linear fitting against range in km;
6. `KDP = 0.5 * dPHIDP/dr` with unit `degree/km`.

Missing gaps split segments. Long segments must never be bridged through NaN
regions or absent gates. When system phase or fit support is unavailable, the
derived fields stay unavailable rather than silently defaulting to zero.

## Fail-closed boundary

- Raw PHIDP remains preserved in degree semantics even when every derived gate
  is unavailable.
- Short segments, missing system phase support, invalid range coordinates,
  invalid phase units or insufficient fit windows do not produce fake KDP.
- Worker integration remains disabled until PHIDP units, period, system-phase
  metadata and representative real cases are frozen independently.
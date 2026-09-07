# Radar Attenuation Artifact 1.0

`RadarAttenuationArtifact` is an immutable offline artifact for the C2 shadow
attenuation slice. It records how raw `DBZH` and trusted `KDP` were converted
into specific attenuation, two-way path-integrated correction, and corrected
reflectivity without changing the operational QC worker.

## Identity and provenance

Every artifact records:

- the frozen shadow attenuation profile version and artifact contract version;
- the source normalized radar-volume contract version, source PHIDP/KDP shadow
  profile version, immutable source URI, and source manifest SHA-256;
- the radar identifier, scan identifier, KDP input unit, and configured
  coefficient source;
- per-sweep summaries of available gate counts, segment counts, capped gates,
  correction magnitudes, and skip reasons;
- `operational_eligible=false` because this artifact is offline evidence, not
  a production release gate.

## Processing semantics

The C2 shadow attenuation slice is fixed to:

1. trusted finite gate selection using raw `DBZH`, trusted `KDP`, and any
   verified blockage mask supplied to the adapter;
2. contiguous valid-segment partitioning with reset on unavailable gates;
3. specific attenuation evaluation using `A_h = a * KDP^b`;
4. two-way trapezoidal path integration in range-km space;
5. explicit caps on both cumulative attenuation correction and corrected
   reflectivity.

Missing KDP gates, unavailable blockage checks, or blockage above the allowed
threshold split segments. Corrections do not carry across those gaps. Blocked
or untrusted gates remain unavailable rather than being amplified.

## Fail-closed boundary

- Unconfigured coefficients produce no corrected reflectivity and remain an
  explicit skip state.
- Shadow results must not be treated as station calibration truth, wet-radome
  detection, or operational attenuation correction.
- Worker integration remains disabled until verified S-band coefficients,
  representative overlap cases, and an independent calibration reference are
  frozen.
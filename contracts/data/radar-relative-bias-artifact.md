# Radar Relative Bias Artifact 1.0

`RadarRelativeBiasArtifact` is an immutable offline artifact for the C2 shadow
inter-radar relative-bias slice. It records only signed `DBZH` differences on
time/geometry/blockage-comparable precipitation gates and must not designate either radar as truth.

## Identity and provenance

Every artifact records:

- the frozen shadow relative-bias profile version and artifact contract version;
- the source normalized radar-volume contract version, immutable source URI,
  source artifact SHA-256, and QC profile version used to derive hard-ray
  exclusions;
- the primary radar identifier, scan identifier, comparison field name, and
  grouping mode (`process_id` or `scan_id_fallback`);
- one comparison entry per reference radar with signed bias summaries,
  comparable gate counts, sweep-level skip reasons, and time offsets;
- `operational_eligible=false` because this artifact is offline evidence, not
  a production release gate.

## Processing semantics

The C2 shadow relative-bias slice is fixed to:

1. compare only raw `DBZH` on time/geometry/blockage-comparable precipitation
   gates;
2. measure signed bias as `primary minus reference` in dBZ;
3. exclude reference rays that carry a local hard radial-interference flag;
4. require verified EGM2008 geometry and a verified DEM-backed blockage check;
5. aggregate engineering summaries by process when `process_id` exists,
   otherwise mark the result as a `scan_id_fallback` engineering-only view.

This artifact reports relative differences only. It does not assert absolute
calibration truth, does not apply gain correction, and does not change any QC
worker field.

## Fail-closed boundary

- Missing geometry resources, DEM support, comparable gates, or time alignment
  keep the comparison in an explicit skip state.
- Results that fall back to scan identifiers remain engineering-only and are
  not sufficient for promotion.
- Worker integration remains disabled until an independent absolute reference,
  process-grouped overlap evidence, and acceptance criteria are frozen.
- `truth_designation_enabled` must remain false and the artifact must not
  designate either radar as truth.

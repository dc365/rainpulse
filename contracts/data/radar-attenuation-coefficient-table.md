# Radar Attenuation Coefficient Table 1.0

`RadarAttenuationCoefficientTable` is the versioned site/band coefficient table
for the C2-P3 shadow calibration boundary. It records verified attenuation
coefficients that may be replayed offline without enabling operational QC
correction.

## Identity and provenance

Every table records:

- the calibration shadow profile version, artifact contract version, radar
  band, and table version;
- a reference to the calibration manifest version that supplied disjoint
  fitting and validation evidence;
- one entry per radar site with coefficient state, `A_h = a * KDP^b`
  parameters, fitted process IDs, validation process IDs, and applicable
  temperature range.

## Entry semantics

- `verified_shadow_use` means the coefficients are eligible only for offline
  shadow replay.
- Every entry must reference a disjoint independent calibration manifest.
- Fitted and validation process IDs remain explicit provenance, not inferred
  metadata.
- The applicable temperature range must be declared for every frozen entry.

## Fail-closed boundary

- Entries without positive coefficients, temperature bounds, or split
  provenance are invalid.
- The table must not enable QI_ATTENUATION or QI_CALIBRATION.
- Worker integration remains disabled until representative Fujian acceptance is
  frozen for the selected site/band coefficients.
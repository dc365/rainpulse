# QCRadarVolume Zarr contract

`QCRadarVolume` is the quality-controlled polar volume. QC runs before
grid interpolation so interference and clutter are not spread into adjacent
grid cells. The original `RawRadarAsset` and `NormalizedRadarVolume` remain
immutable.

## Geometry

The volume uses the exact `sweep`/`ray`/`gate` representation of its
`NormalizedRadarVolume`. Every required polar array has dimensions
`[ray, gate]`; sweep indices and station coordinates are unchanged.

## Variables

| Variable | Dtype | Unit/range | Required |
|---|---:|---|---|
| `DBZH_RAW` | float32 | dBZ; decoded DBZH before QC | yes |
| `DBZH_QC` | float32 | dBZ; `NaN` where invalid | yes |
| `DBZH_TEXTURE` | float32 | dBZ; local MAD texture over the B1 polar window | when produced |
| `DBZH_TEXTURE_SUPPORT_RATE` | float32 | `[0, 1]`; fraction of valid B1 window samples | when produced |
| `DBZH_TEXTURE_AVAILABLE_MASK` | uint8 | Exactly 0 or 1 | when produced |
| `ZDR_QC` | float32 | dB | if produced |
| `ZDR_TEXTURE` | float32 | dB; local MAD texture over the B1 polar window | when produced |
| `ZDR_TEXTURE_SUPPORT_RATE` | float32 | `[0, 1]`; fraction of valid B1 window samples | when produced |
| `ZDR_TEXTURE_AVAILABLE_MASK` | uint8 | Exactly 0 or 1 | when produced |
| `PHIDP_QC` | float32 | degree | if produced |
| `PHIDP_SHADOW_UNWRAPPED` | float32 | degree; segment-local unwrapped PHIDP; `NaN` where unavailable | when C1 shadow phase-processing is configured |
| `PHIDP_SHADOW_CORRECTED` | float32 | degree; unwrapped PHIDP after system-phase subtraction; `NaN` where unavailable | when C1 shadow phase-processing is configured |
| `PHIDP_SHADOW_TRUSTED_GATE_MASK` | uint8 | Exactly 0 or 1; 1 only where the C1 shadow adapter accepted the input gate | when C1 shadow phase-processing is configured |
| `PHIDP_SHADOW_SEGMENT_INDEX` | int32 | `-1` where unavailable or excluded; non-negative contiguous valid-segment identifier otherwise | when C1 shadow phase-processing is configured |
| `PHIDP_CIRCULAR_VARIANCE` | float32 | `[0, 1]`; local circular variance over the B1 polar window | when produced |
| `PHIDP_CIRCULAR_VARIANCE_SUPPORT_RATE` | float32 | `[0, 1]`; fraction of valid B1 window samples | when produced |
| `PHIDP_CIRCULAR_VARIANCE_AVAILABLE_MASK` | uint8 | Exactly 0 or 1 | when produced |
| `KDP` | float32 | degree km-1 | if produced |
| `KDP_SHADOW` | float32 | degree km-1; robust local-fit diagnostic KDP; `NaN` where unavailable | when C1 shadow phase-processing is configured |
| `KDP_SHADOW_UNCERTAINTY` | float32 | degree km-1; local-fit uncertainty; `NaN` where unavailable | when C1 shadow phase-processing is configured |
| `KDP_SHADOW_AVAILABLE_MASK` | uint8 | Exactly 0 or 1 | when C1 shadow phase-processing is configured |
| `SPECIFIC_ATTENUATION_SHADOW` | float32 | dB km-1; diagnostic specific attenuation derived only from `KDP_SHADOW`; `NaN` where unavailable | when C2 shadow attenuation is configured |
| `ATTENUATION_CORRECTION_SHADOW` | float32 | dB; diagnostic path-integrated attenuation correction; `NaN` where unavailable | when C2 shadow attenuation is configured |
| `DBZH_ATTENUATION_SHADOW_CORRECTED` | float32 | dBZ; `DBZH_RAW` plus the diagnostic shadow attenuation correction; `NaN` where unavailable | when C2 shadow attenuation is configured |
| `ATTENUATION_SHADOW_AVAILABLE_MASK` | uint8 | Exactly 0 or 1 | when C2 shadow attenuation is configured |
| `ATTENUATION_SHADOW_SEGMENT_INDEX` | int32 | `-1` where unavailable or excluded; non-negative contiguous attenuation-segment identifier otherwise | when C2 shadow attenuation is configured |
| `RHOHV_TEXTURE` | float32 | 1; local MAD texture over the B1 polar window | when produced |
| `RHOHV_TEXTURE_SUPPORT_RATE` | float32 | `[0, 1]`; fraction of valid B1 window samples | when produced |
| `RHOHV_TEXTURE_AVAILABLE_MASK` | uint8 | Exactly 0 or 1 | when produced |
| `VR_QC` | float32 | m s-1 | if produced |
| `QUALITY_INDEX` | float32 | `[0, 1]`; `NaN` where unavailable | yes |
| `QI_METEO` | float32 | `[0, 1]`; `NaN` when unavailable | yes |
| `QI_BLOCKAGE` | float32 | `[0, 1]`; `NaN` until RP-009 | yes |
| `QI_BEAM_HEIGHT` | float32 | `[0, 1]`; `NaN` until RP-009 | yes |
| `QI_ATTENUATION` | float32 | `[0, 1]`; `NaN` when not evaluated | yes |
| `QI_INTERFERENCE` | float32 | `[0, 1]`; `NaN` when unavailable | yes |
| `QI_TIME` | float32 | `[0, 1]`; `NaN` until analysis-time alignment | yes |
| `QI_CALIBRATION` | float32 | `[0, 1]`; `NaN` without verified calibration | yes |
| `QI_RANGE` | float32 | `[0, 1]`; `NaN` when unavailable | yes |
| `QC_FLAGS` | uint32 | Versioned bit set | yes |
| `VALID_MASK` | uint8 | Exactly 0 or 1 | yes |
| `LOW_QUALITY_MASK` | uint8 | Exactly 0 or 1 and never greater than valid | yes |
| `BLOCKAGE_RATE` | float32 | `[0, 1]` | yes after DEM processing |
| `ATTENUATION_CORRECTION` | float32 | dB | when future operational attenuation module runs |
| `P_METEO` | float32 | `[0, 1]` | yes in Phase 1 |
| `P_AP` | float32 | `[0, 1]`; `NaN` when prerequisites are unavailable | yes in Phase 1 |
| `P_SEA_CLUTTER` | float32 | `[0, 1]`; `NaN` when prerequisites are unavailable | yes in Phase 1 |
| `P_RADIAL_INTERFERENCE` | float32 | `[0, 1]`; `NaN` when unavailable | yes in Phase 1 |
| `P_METEO_DUAL_POL` | float32 | `[0, 1]`; optional diagnostic fuzzy probability | when enabled |
| `P_VERTICAL_CONSISTENCY` | float32 | `[0, 1]`; `NaN` when no B2-comparable higher beam exists | when enabled |
| `P_VERTICAL_CONSISTENCY_AVAILABLE_MASK` | uint8 | Exactly 0 or 1; 1 only where B2 beam geometry supports the comparison | when enabled in evidence-v2 |
| `VERTICAL_HEIGHT_DIFFERENCE_M` | float32 | m; absolute centre-height difference of the B2 beam pair; `NaN` when unavailable | when enabled in evidence-v2 |
| `P_CROSS_RADAR_TRUSTED_SUPPORT` | float32 | `[0, 1]`; trusted neighbour echo support fraction; `NaN` when unavailable | when context fusion is enabled in evidence-v2 |
| `P_CROSS_RADAR_TRUSTED_AVAILABLE_MASK` | uint8 | Exactly 0 or 1; 1 only where trusted neighbour support is geometrically comparable | when context fusion is enabled in evidence-v2 |
| `INTERFERENCE_TYPE` | uint8 | `0` none, `1` narrow, `2` broad, `3` intermittent, `4` short-range, `5` reverse | when morphology detection is enabled |

`QC_FLAGS` follows the versioned `configs/qc/flag-definitions.yaml` definition.
Missing, low-quality, and valid no-rain remain separate states. A QC module may
repair a limited area only when it retains the cause flag, sets `CORRECTED`,
and publishes its correction/confidence diagnostic.

An unavailable prerequisite is represented by `NaN` plus a `skipped` module
record. Zero means the module ran and found zero probability; it must never be
used as a substitute for an absent clutter map, coastline mask, DEM,
calibration value, field, neighbouring radar, or adjacent volume. The first
quality index combines only components selected by its versioned profile and
records the component availability mask in module diagnostics.

The C1 shadow phase-processing fields are diagnostic-only. When configured they
may publish `PHIDP_SHADOW_*` and `KDP_SHADOW*` arrays plus a
`phase_processing_shadow` module record, but must not change `DBZH_QC`, `QC_FLAGS`, `QUALITY_INDEX`,
or any `QI_*` field. The shadow module remains non-operational until an explicit
phase-processing profile is supplied and that profile keeps
`worker_integration_enabled=false`.

The C2 shadow attenuation fields are also diagnostic-only. When configured they
may publish `SPECIFIC_ATTENUATION_SHADOW`, `ATTENUATION_CORRECTION_SHADOW`,
`DBZH_ATTENUATION_SHADOW_CORRECTED`, `ATTENUATION_SHADOW_AVAILABLE_MASK`, and
`ATTENUATION_SHADOW_SEGMENT_INDEX` plus an `attenuation_shadow` module record,
but must not change `QI_ATTENUATION`, `QI_CALIBRATION`, `DBZH_QC`, `QC_FLAGS`,
or `QUALITY_INDEX`. If no attenuation profile is supplied then no attenuation
shadow fields are written. If a profile is supplied but `KDP_SHADOW` is
unavailable, or the attenuation coefficients remain unconfigured, the published
shadow fields stay fail-closed as `NaN`/`0`/`-1` and the module record remains
`skipped`.

For evidence-v2 B2 diagnostics, vertical comparison is available only when the
current radar has verified EGM2008-compatible altitude metadata and the next
higher sweep overlaps the current beam's vertical support with an absolute
centre-height difference of at most `500 m`. `P_VERTICAL_CONSISTENCY`
unavailable gates must remain `NaN`; they are not back-filled with zero.

`P_CROSS_RADAR_TRUSTED_SUPPORT` is a diagnostic trusted mask, not an automatic
negative-promotion signal. A gate is comparable only when the neighbouring
radar has verified compatible geometry, valid reference DBZH, acceptable radar
health, cumulative terrain blockage at most `0.30`, and no local hard
radial-interference flag on the matched reference ray.

The B1 `polar_texture` diagnostics use a `1.75 km` range window, an azimuth
half-window of `±1.5°`, and require at least `70%` valid samples in the local
polar neighbourhood before publishing a texture or circular-variance value.
`DBZH_TEXTURE`, `ZDR_TEXTURE`, and `RHOHV_TEXTURE` use the MAD scale
`1.4826 × median(|x - median(x)|)` rather than a standard deviation.

## Required attributes and module provenance

Required root attributes are `contract_name=rainpulse.qc-radar-volume`,
`contract_version=1.0`, `asset_id`, `scan_id`, `radar_id`,
`normalized_volume_uri`, `radar_config_version`, `qc_profile`,
`qc_pipeline_version`, `decision_version`, `flag_definition_version`, `dem_asset_version`,
`clutter_map_version`, and creation time in UTC.

Optional QC provenance attributes may include `context_fingerprint` and a
serialized ordered `radial_context` manifest that freezes the primary input,
validated temporal/cross-radar context identities, and any skip reasons used
by replay or scientific audit tooling.

Each attempted module records its name, version, status
(`applied`, `skipped`, or `failed`), input fields, skip/failure reason, metrics,
and produced variables. Missing prerequisites result in `skipped`; they never
cause an absent field to be fabricated.

Publication is temporary-write, full validation, then atomic commit to
`radar/qc/{radar_id}/{scan_id}/{qc_pipeline_version}/volume.zarr`.

The pipeline version is part of the immutable object prefix so a versioned rerun cannot
reuse another job's `_SUCCESS.json` completion marker.

## Observed polar area

`radial_interference_area_km2` in the QC summary is nullable. Its companion
`radial_interference_area_status` is `computed` only when verified horizontal
beam width is available, otherwise `unavailable`. Unknown area is not zero and
is omitted from numeric-only worker metrics. The definition is
`sum_of_observed_polar_wedges_per_elevation`: gaps between rays are capped by
the horizontal beam width and duplicate azimuths do not duplicate coverage.
This sum is not a ground-area union across elevations. Acceptance NPZ inputs
may supply a scalar `beam_width_deg`; without it, pollution area is unavailable.

# RadarAnalysis Zarr contract

`RadarAnalysis` is the quality-aware multi-radar observation for one fixed UTC
analysis time after RP-011 QPE. The active contract is
`contract_version=1.2`. RP-010 first publishes an immutable
`RadarMosaic`; the QPE step consumes it and adds `RATE_QPE` plus its versioned
diagnostics before atomically publishing `RadarAnalysis`. `RadarAnalysis` is the
only source accepted by `NowcastInput` construction.

## Dimensions and coordinates

Dimensions are `lat × lon`; both one-dimensional coordinates, CRS, inclusive
coordinate-centre bounds, masks, intervals, values, and dtype exactly match the
immutable `grid_id`. Phase 1 uses EPSG:4326 and strictly increasing latitude
and longitude. `analysis_time` is one fixed five-minute UTC boundary.

Every contributing single-radar `RadarGrid` uses these same coordinates. Adding
radars increases the number of input grids and contributors, not the dimensions
of the fused `RadarAnalysis`. No second spatial interpolation is allowed between
single-radar gridding, mosaic, QPE, and application-product export.

## Variables

| Variable | Dtype | Unit/range | Required |
|---|---:|---|---|
| `DBZH_QC` | float32 | dBZ | yes |
| `REF_NOWCAST` | float32 | dBZ | yes |
| `RATE_QPE` | float32 | mm h-1, non-negative | yes |
| `QUALITY_INDEX` and all `QI_*` components | float32 | `[0, 1]` | yes |
| `QC_FLAGS` | uint32 | versioned bit set | yes |
| `SOURCE_RADAR` | uint16 | code table in attributes; 0 reserved for no source | yes |
| `CONTRIBUTOR_COUNT` | uint8 | inputs used at the cell | yes |
| `SOURCE_ELEVATION` | float32 | degree | yes |
| `BEAM_HEIGHT` | float32 | metres above MSL | yes |
| `TERRAIN_HEIGHT` | float32 | metres above MSL | yes |
| `BLOCKAGE_RATE` | float32 | `[0, 1]` | yes |
| `DATA_AGE` | float32 | non-negative minutes | yes |
| `VALID_MASK` | uint8 | exactly 0 or 1 | yes |
| `LOW_QUALITY_MASK` | uint8 | exactly 0 or 1 | yes |

The required QI components are `QI_METEO`, `QI_BLOCKAGE`, `QI_BEAM_HEIGHT`,
`QI_ATTENUATION`, `QI_INTERFERENCE`, `QI_TIME`, `QI_CALIBRATION`, and
`QI_RANGE`.

The v1.1 design narrative listed `DBZH_RAW` and `INTERFERENCE_TYPE`, but the
accepted RP-010 mosaic cannot retain a lossless raw reflectivity field after
multi-radar linear-Z blending and currently carries interference provenance in
`QC_FLAGS`/`QI_INTERFERENCE`. Those fields are optional in v1.2 and must not be fabricated
by copying `DBZH_QC` or inventing an interference class. A later
contract may make them required only after the upstream chain provides their
lossless semantics.

## Phase-1 basic QPE

The versioned Phase-1 relation is configured as `Z = a R^b`, where
`Z=10^(dBZ/10)` and `R=(Z/a)^(1/b)` in `mm h-1`. The coefficient, exponent,
no-rain dBZ threshold and maximum reportable rate are immutable configuration
values. Values below the configured rain threshold remain valid observations
with `RATE_QPE=0`; values above the configured cap are capped and counted in
the QPE summary rather than hidden. Phase-1 gauge adjustment is disabled until
quality-controlled station observations and rules are supplied.

## Optional stratiform VPR correction

When a dedicated QPE profile explicitly enables VPR, `RadarAnalysis` may add
the optional fields `DBZH_VPR_INPUT`, `DBZH_VPR_CORRECTED`, `VPR_CORRECTION_DB`,
`VPR_UNCERTAINTY_DB`, `VPR_APPLIED_MASK`, `VPR_STRATIFORM_MASK`,
`VPR_OVERSHOOT_MASK`, `PRECIP_TYPE`, `MELTING_LAYER_BOTTOM_HEIGHT`, and
`MELTING_LAYER_TOP_HEIGHT`. `DBZH_VPR_INPUT` preserves the original mosaic
reflectivity, including observations subsequently rejected as overshoot. The
immutable source mosaic remains available through `input_mosaic_uri`.
`DBZH_QC` retains uncorrected mosaic reflectivity only in cells that remain
valid; the optional corrected field records reflectivity used for rainfall
conversion. All required floating-point fields become NaN in missing cells;
`LOW_QUALITY_MASK`, `SOURCE_RADAR`, and `CONTRIBUTOR_COUNT` become zero there.
Optional VPR input and decision diagnostics retain evidence in rejected cells
and must never be interpreted as valid nowcast input.

This correction is allowed only in stratiform scenes and only when the input
`RadarMosaic` already carries explicit precipitation type plus melting-layer
bottom/top height fields from trusted upstream data. A VPR-enabled profile must
fail closed when those explicit inputs are absent; it must not guess a freezing
level height from one static threshold or silently fall back to fabricated
mixed-phase deletion.

Cells inside the melting layer may set `BRIGHT_BAND` and `CORRECTED` in
`QC_FLAGS` while recording the applied dB reduction. Stratiform cells above the
melting layer may use the versioned extrapolation rule recorded in attributes
and summary metadata. Any far-range overshoot without supported near-surface
observation must remain missing: `VPR_OVERSHOOT_MASK=1`, `VALID_MASK=0`, and
`RATE_QPE=NaN`. Convective cells remain passthrough by default; stratiform VPR
must not be applied outside the configured scene class.

## Fusion invariants

- Invalid, fully blocked, or seriously interfered inputs are excluded.
- Similar-quality reflectivity inputs are converted `dBZ → linear Z`, blended,
  then converted back. Direct dBZ averaging is forbidden.
- Every cell retains its source radar/elevation, beam height, data age, flags,
  and QI components. Blended cells use the documented source code and retain
  the contributor list in chunk/analysis provenance.
- A missing radar degrades coverage but does not automatically fail the
  analysis cycle.
- Missing cells have `VALID_MASK=0` and `NaN` reflectivity/rain rate. Valid
  no-rain has `VALID_MASK=1` and `RATE_QPE=0`.

## Required attributes and publication

Attributes include `contract_name=rainpulse.radar-analysis`,
`contract_version=1.2`, `analysis_id`, `analysis_time`, `grid_id`, CRS,
`analysis_cycle_version`, `radar_scan_ids`, actual per-radar time offsets,
`qc_pipeline_versions`, `grid_config_version`, `mosaic_config_version`,
`qpe_config_version`, `flag_definition_version`, source/interference code
tables, coverage/quality summaries, and creation time in UTC.

Publication is atomic and version-isolated under
`analysis/{grid_id}/{yyyy}/{mm}/{dd}/{analysis_time}/{qpe_algorithm_version}/analysis.zarr`.

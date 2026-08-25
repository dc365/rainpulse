# RadarAnalysis Zarr contract

`RadarAnalysis` is the quality-aware multi-radar observation for one fixed UTC
analysis time. It is the only source accepted by `NowcastInput` construction.

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
| `DBZH_RAW` | float32 | dBZ | yes |
| `DBZH_QC` | float32 | dBZ | yes |
| `REF_NOWCAST` | float32 | dBZ | yes |
| `RATE_QPE` | float32 | mm h-1, non-negative | yes |
| `QUALITY_INDEX` and all `QI_*` components | float32 | `[0, 1]` | yes |
| `QC_FLAGS` | uint32 | versioned bit set | yes |
| `SOURCE_RADAR` | uint16 | code table in attributes; 0 reserved for no source | yes |
| `SOURCE_ELEVATION` | float32 | degree | yes |
| `BEAM_HEIGHT` | float32 | metres above MSL | yes |
| `BLOCKAGE_RATE` | float32 | `[0, 1]` | yes |
| `DATA_AGE` | float32 | non-negative minutes | yes |
| `INTERFERENCE_TYPE` | uint8 | code table in attributes | yes |
| `VALID_MASK` | uint8 | exactly 0 or 1 | yes |
| `LOW_QUALITY_MASK` | uint8 | exactly 0 or 1 | yes |

The required QI components are `QI_METEO`, `QI_BLOCKAGE`, `QI_BEAM_HEIGHT`,
`QI_ATTENUATION`, `QI_INTERFERENCE`, `QI_TIME`, `QI_CALIBRATION`, and
`QI_RANGE`.

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
`contract_version=1.1`, `analysis_id`, `analysis_time`, `grid_id`, CRS,
`analysis_cycle_version`, `radar_scan_ids`, actual per-radar time offsets,
`qc_pipeline_versions`, `grid_config_version`, `mosaic_config_version`,
`qpe_config_version`, `flag_definition_version`, source/interference code
tables, coverage/quality summaries, and creation time in UTC.

Publication is atomic under
`analysis/{grid_id}/{yyyy}/{mm}/{dd}/{analysis_time}/analysis.zarr`.

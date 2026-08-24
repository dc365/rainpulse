# RadarGrid Zarr contract

`RadarGrid` is one radar's two-dimensional Hybrid Scan on the registered target
grid. Only validated `QCRadarVolume` data may be gridded. The selected source
elevation can vary by azimuth and range; Phase 1 must not assume a permanently
fixed 0.5-degree elevation.

## Dimensions, coordinates, and variables

Dimensions are `y × x`. Coordinates are projected cell-centre northing/easting
in metres and must exactly match the immutable `grid_id`.

| Variable | Dtype | Unit/range | Required |
|---|---:|---|---|
| `DBZH_QC` | float32 | dBZ | yes |
| `QUALITY_INDEX` | float32 | `[0, 1]` | yes |
| `QC_FLAGS` | uint32 | versioned bit set | yes |
| `SOURCE_ELEVATION` | float32 | degree | yes |
| `BEAM_HEIGHT` | float32 | metres above MSL | yes |
| `BLOCKAGE_RATE` | float32 | `[0, 1]` | yes |
| `DATA_AGE` | float32 | non-negative minutes | yes |
| `VALID_MASK` | uint8 | exactly 0 or 1 | yes |
| `LOW_QUALITY_MASK` | uint8 | exactly 0 or 1 | yes |

Required attributes include `contract_name=rainpulse.radar-grid`,
`contract_version=1.0`, `radar_id`, `scan_id`, `grid_id`, CRS, bounds,
resolution, scan time, `radar_config_version`, `qc_pipeline_version`,
`grid_config_version`, Hybrid Scan algorithm/version, and all input object
URIs/identities.

Invalid or completely blocked cells remain missing. Severe blockage must not be
compensated by a large reflectivity multiplier. Publication is atomic under
`radar/grid/{radar_id}/{yyyy}/{mm}/{dd}/{scan_time}/grid.zarr`.

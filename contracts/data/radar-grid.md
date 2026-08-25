# RadarGrid Zarr contract

`RadarGrid` is one radar's two-dimensional Hybrid Scan on the registered target
grid. Only validated `QCRadarVolume` data may be gridded. The selected source
elevation can vary by azimuth and range; Phase 1 must not assume a permanently
fixed 0.5-degree elevation.

## Dimensions and coordinates

Dimensions are `lat × lon`. `lat` and `lon` are one-dimensional `float32`
coordinates in degrees north/east and must exactly match the immutable
`grid_id`. Both axes are strictly increasing. The registered Phase 1 grid is
`fuzhou_118_123_25_27_0p01deg_v1`:

| Coordinate | Start/end | Interval | Count |
|---|---:|---:|---:|
| `lon` | `118.00 … 123.00` | `0.01°` | `501` |
| `lat` | `25.00 … 27.00` | `0.01°` | `201` |

The coordinate endpoints are grid-point centres, not raster outer edges.
Longitude and latitude intervals are angular and must never be represented as
one constant square-cell `resolution_m`. Algorithms that need distances use a
versioned geodesic or local projected metric while retaining this output grid.

## Variables

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
`contract_version=1.1`, `radar_id`, `scan_id`, `grid_id`, `crs=EPSG:4326`,
inclusive coordinate-centre bounds, `longitude_interval_deg`,
`latitude_interval_deg`, scan time, `radar_config_version`, `qc_pipeline_version`,
`grid_config_version`, Hybrid Scan algorithm/version, and all input object
URIs/identities.

Invalid or completely blocked cells remain missing. Severe blockage must not be
compensated by a large reflectivity multiplier. Publication is atomic under
`radar/grid/{radar_id}/{yyyy}/{mm}/{dd}/{scan_time}/grid.zarr`.

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
| `ZDR_QC` | float32 | dB | if produced |
| `PHIDP_QC` | float32 | degree | if produced |
| `KDP` | float32 | degree km-1 | if produced |
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
| `ATTENUATION_CORRECTION` | float32 | dB | when attenuation module runs |
| `P_METEO` | float32 | `[0, 1]` | yes in Phase 1 |
| `P_AP` | float32 | `[0, 1]`; `NaN` when prerequisites are unavailable | yes in Phase 1 |
| `P_SEA_CLUTTER` | float32 | `[0, 1]`; `NaN` when prerequisites are unavailable | yes in Phase 1 |
| `P_RADIAL_INTERFERENCE` | float32 | `[0, 1]`; `NaN` when unavailable | yes in Phase 1 |

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

## Required attributes and module provenance

Required root attributes are `contract_name=rainpulse.qc-radar-volume`,
`contract_version=1.0`, `asset_id`, `scan_id`, `radar_id`,
`normalized_volume_uri`, `radar_config_version`, `qc_profile`,
`qc_pipeline_version`, `flag_definition_version`, `dem_asset_version`,
`clutter_map_version`, and creation time in UTC.

Each attempted module records its name, version, status
(`applied`, `skipped`, or `failed`), input fields, skip/failure reason, metrics,
and produced variables. Missing prerequisites result in `skipped`; they never
cause an absent field to be fabricated.

Publication is temporary-write, full validation, then atomic commit to
`radar/qc/{radar_id}/{scan_id}/{qc_pipeline_version}/volume.zarr`.

The pipeline version is part of the immutable object prefix so a versioned rerun cannot
reuse another job's `_SUCCESS.json` completion marker.

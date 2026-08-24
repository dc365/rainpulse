# NormalizedRadarVolume Zarr contract

`NormalizedRadarVolume` is the decoder output for one physical radar volume.
It preserves the original polar sampling geometry and canonicalizes field
names, units, scale, missing values, and timestamps. It does not perform
meteorological QC.

## Logical dimensions and coordinates

The logical dimensions are `sweep`, `ray`, and `gate`. Rays from all sweeps are
stored on one `ray` dimension; sweep start/end indices identify each contiguous
subset without padding or silently resampling the source scan.

| Coordinate | Dimensions | Dtype | Requirements |
|---|---|---|---|
| `azimuth` | `[ray]` | float32 | Degrees clockwise from true north, normalized to `[0, 360)` |
| `elevation` | `[ray]` | float32 | Degrees above the local horizontal |
| `ray_time` | `[ray]` | datetime64[ns] | UTC and non-decreasing within each sweep |
| `range` | `[gate]` | float32 | Gate-centre slant range in metres, strictly increasing |
| `sweep_number` | `[sweep]` | int16 | Unique and increasing |
| `sweep_start_ray_index` | `[sweep]` | int32 | Inclusive ray index |
| `sweep_end_ray_index` | `[sweep]` | int32 | Inclusive ray index, not before start |

If source sweeps use incompatible gate spacing or gate counts, the adapter must
preserve them in separate versioned Zarr sweep groups rather than invent a
common geometry. The selected encoding is recorded in `geometry_encoding`.

## Canonical fields

Each available moment is `[ray, gate]` `float32` with `NaN` for source missing
values. Only fields present and verified in the radar configuration are
written.

| Variable | Canonical unit | Required |
|---|---|---|
| `DBZH` | dBZ | yes for a ready Phase 1 radar |
| `ZDR` | dB | optional |
| `RHOHV` | 1 | optional |
| `PHIDP` | degree | optional |
| `VR` | m s-1 | optional |
| `SW` | m s-1 | optional |
| `SNR` | dB | optional |

No decoder may synthesize an absent optional field. Raw integer codes, scale,
offset, missing values, and source units remain recorded in the field-mapping
metadata.

## Required attributes

`contract_name=rainpulse.normalized-radar-volume`, `contract_version=1.0`,
`asset_id`, `radar_id`, `radar_config_version`, `decoder_id`,
`decoder_version`, `source_format`, `source_format_version`,
`field_mapping_version`, `geometry_encoding`, station longitude/latitude/
altitude and altitude datum, radar band, scan strategy, volume start/end UTC,
and the input SHA-256.

## Validation and publication

- Geometry, units, field ranges, sweep boundaries, and time coverage are
  validated before publication.
- Source missing values become `NaN`, never zero.
- The decoder writes below `_temporary/{job_id}`, validates the complete Zarr
  hierarchy, and publishes `_SUCCESS.json` last.
- Output is stored at
  `radar/normalized/{radar_id}/{yyyy}/{mm}/{dd}/{scan_time}/volume.zarr`.

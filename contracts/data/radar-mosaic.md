# RadarMosaic Zarr contract

`RadarMosaic` is the RP-010 quality-aware, time-aligned reflectivity mosaic.
It is an immutable intermediate between committed single-radar `RadarGrid`
artifacts and RP-011 QPE. It deliberately does not contain `RATE_QPE`;
`RadarAnalysis` is published only after a versioned QPE step has run.

## Coordinates and time

Dimensions are `lat × lon`. Coordinates and `coordinate_sha256` exactly match
the immutable `grid_id`; no second spatial interpolation is allowed.
`analysis_time` is a five-minute UTC boundary. Every contributor records the
actual integer offset of its volume end time from the analysis time.

## Required variables

| Variable | Dtype | Semantics |
|---|---:|---|
| `DBZH_QC`, `REF_NOWCAST` | float32 | dBZ; missing is `NaN` |
| `QUALITY_INDEX`, all `QI_*` | float32 | `[0,1]`; unavailable component is `NaN` |
| `QC_FLAGS` | uint32 | bitwise provenance from selected/blended inputs |
| `SOURCE_RADAR` | uint16 | 0 missing, 65535 blended, other codes in attributes |
| `CONTRIBUTOR_COUNT` | uint8 | number of inputs used at the cell |
| `SOURCE_ELEVATION` | float32 | degree |
| `BEAM_HEIGHT`, `TERRAIN_HEIGHT` | float32 | m MSL |
| `BLOCKAGE_RATE` | float32 | `[0,1]` |
| `DATA_AGE` | float32 | non-negative minutes from alignment |
| `VALID_MASK`, `LOW_QUALITY_MASK` | uint8 | exactly 0 or 1 |

The QI components are `QI_METEO`, `QI_BLOCKAGE`, `QI_BEAM_HEIGHT`,
`QI_ATTENUATION`, `QI_INTERFERENCE`, `QI_TIME`, `QI_CALIBRATION`, and
`QI_RANGE`.

## Fusion invariants

- Invalid or rejected inputs never contribute.
- If the best QI is clearly higher, that radar is selected.
- Similar-quality inputs are converted `dBZ → linear Z`, weighted by QI,
  blended, and converted back. Direct dBZ averaging is forbidden.
- Blended metadata and available QI components use the same normalized
  weights; `QC_FLAGS` is the bitwise OR and the source code is 65535.
- Missing cells have `VALID_MASK=0`, source code/count 0, and `NaN` floating
  fields. Valid no-echo is never created from missing coverage.
- A missing or excluded radar degrades the cycle but does not automatically
  fail it when the configured minimum contributor count is met.

## Publication

Required attributes include contract/config/algorithm versions, analysis and
grid identities, contributor scan IDs, time offsets, radar source code table,
actual contributor/exclusion provenance, coverage/quality summaries, input
operational eligibility and UTC creation time.

Publication is atomic under:

```text
analysis/mosaic/{grid_id}/{yyyy}/{mm}/{dd}/{analysis_time}/{mosaic_algorithm_version}/mosaic.zarr
```

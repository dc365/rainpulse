# NowcastInput Zarr contract

`NowcastInput` is the canonical hand-off from preprocessing to every RainPulse
model adapter. The logical contract is independent of Zarr v2/v3 encoding and
compressor selection; those storage details are versioned by the preprocessing
configuration.

## Dimensions and coordinates

The required dimension order is `time × y × x`.

| Coordinate | Type | Requirements |
|---|---|---|
| `time` | `datetime64[ns]` | UTC, strictly increasing, no duplicates |
| `y` | `float64` | Projected cell-centre northing in metres |
| `x` | `float64` | Projected cell-centre easting in metres |

Phase 1 inputs contain 3–6 frames at an exact five-minute interval. The final
`time` coordinate equals `issue_time_utc`. Mixing source intervals within one
model input is invalid.

## Variables

All gridded variables have dimensions `[time, y, x]` unless marked optional.

| Variable | Dtype | Units | Range / meaning | Required |
|---|---|---|---|---|
| `DBZH_QC` | `float32` | `dBZ` | Quality-controlled reflectivity; `NaN` where missing | yes |
| `RATE_QPE` | `float32` | `mm h-1` | Instantaneous non-negative rain rate; `NaN` where missing | yes |
| `QUALITY_INDEX` | `float32` | `1` | Closed interval `[0, 1]`; `NaN` where unavailable | yes |
| `VALID_MASK` | `uint8` | `1` | Exactly `0` or `1` | yes |
| `LOW_QUALITY_MASK` | `uint8` | `1` | Exactly `0` or `1`; may be `1` only where valid | yes |
| `QC_FLAGS` | `uint32` | `1` | Bit mask defined by the versioned QC configuration | yes |
| `DATA_AGE` | `float32` | `min` | Non-negative age of the contributing observation | yes |
| `BEAM_HEIGHT` | `float32` | `m` | Beam height above mean sea level | no |

## Required dataset attributes

| Attribute | Type | Meaning |
|---|---|---|
| `contract_name` | string | Must equal `rainpulse.nowcast-input` |
| `contract_version` | string | Must equal `1.1` for this contract |
| `crs` | string | Authoritative CRS identifier or WKT, frozen by grid configuration |
| `grid_id` | string | Immutable identifier for CRS, bounds, resolution and masks |
| `resolution_m` | number | Positive square-cell resolution |
| `timestep_minutes` | integer | Must equal `5` in Phase 1 |
| `issue_time_utc` | string | RFC 3339 UTC timestamp equal to the final time coordinate |
| `source_name` | string | Registered input source identifier |
| `source_version` | string | Source decoder/configuration version |
| `preprocess_version` | string | Reproducible preprocessing implementation version |
| `input_asset_ids` | array[string] | Ordered identifiers of all contributing raw assets |
| `analysis_ids` | array[string] | Ordered `RadarAnalysis` identities for all input frames |
| `qc_pipeline_versions` | array[string] | QC versions represented by the sequence |
| `qpe_config_version` | string | QPE algorithm/configuration used by all frames |

## Missing, no-rain and low-quality states

These states are distinct and must survive every conversion:

| State | `RATE_QPE` | `VALID_MASK` | `LOW_QUALITY_MASK` |
|---|---:|---:|---:|
| Valid no-rain | `0` | `1` | `0` or `1` |
| Valid rain | finite value `> 0` | `1` | `0` or `1` |
| Missing | `NaN` | `0` | `0` |

For every cell where `VALID_MASK == 0`, `RATE_QPE` and `DBZH_QC` must be
`NaN`. A model adapter may derive an imputed working array, but it must retain
the original masks and record the interpolation method in its run metadata.
Missing cells must never be silently converted to zero rainfall.

## Validation invariants

- `x` and `y` lengths and values match the immutable `grid_id` definition.
- All required arrays have identical `[time, y, x]` shapes.
- Rain rate, data age and resolution are never negative when finite.
- `LOW_QUALITY_MASK <= VALID_MASK` element-wise.
- Time coordinates are UTC, regular and exactly five minutes apart in Phase 1.
- Dataset publication is atomic: write under `_temporary/{job_id}`, validate,
  then publish to
  `nowcast-input/{grid_id}/{yyyy}/{mm}/{dd}/{issue_time}/input.zarr`.

Real source names, CRS, bounds and QC thresholds remain intentionally unfrozen
until representative raw radar samples and verified station/grid definitions
are supplied. QC flag bits are frozen by the referenced flag-definition
version.

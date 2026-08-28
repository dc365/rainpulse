# NowcastInput Zarr contract

`NowcastInput` is the canonical hand-off from preprocessing to every RainPulse
model adapter. The logical contract is independent of Zarr v2/v3 encoding and
compressor selection; those storage details are versioned by the preprocessing
configuration.

## Dimensions and coordinates

The required dimension order is `time × lat × lon`.

| Coordinate | Type | Requirements |
|---|---|---|
| `time` | `datetime64[ns]` | UTC, strictly increasing, no duplicates |
| `lat` | `float32` | Degrees north, strictly increasing, identical to `grid_id` |
| `lon` | `float32` | Degrees east, strictly increasing, identical to `grid_id` |

Phase 1 inputs contain 3–6 frames at an exact five-minute interval. The final
`time` coordinate equals `issue_time_utc`. Mixing source intervals within one
model input is invalid.

## Variables

All gridded variables have dimensions `[time, lat, lon]` unless marked optional.

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
| `contract_version` | string | Must equal `1.2` for this contract |
| `crs` | string | Authoritative CRS identifier or WKT, frozen by grid configuration |
| `grid_id` | string | Immutable identifier for CRS, bounds, resolution and masks |
| `longitude_interval_deg` | number | Positive angular longitude interval |
| `latitude_interval_deg` | number | Positive angular latitude interval |
| `grid_metric_version` | string | Versioned degree-to-distance rule used by model adapters |
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

- `lat` and `lon` lengths, values, dtype, and order match the immutable
  `grid_id` definition.
- All required arrays have identical `[time, lat, lon]` shapes.
- Rain rate and data age are never negative when finite; angular intervals are positive.
- `LOW_QUALITY_MASK <= VALID_MASK` element-wise.
- Time coordinates are UTC, regular and exactly five minutes apart in Phase 1.
- Dataset publication is atomic: write immutable content-addressed objects,
  validate them, then conditionally create `_SUCCESS.json`; a concurrent
  duplicate reuses the first committed marker. The stable artifact URI is
  `nowcast-input/{grid_id}/{yyyy}/{mm}/{dd}/{issue_time}/input.zarr`.

Model adapters may work in pixel space, but physical motion conversion must use
the registered `grid_metric_version`; treating `0.01°` as a constant one
kilometre square is invalid. QC thresholds remain versioned independently. QC
flag bits are frozen by the referenced flag-definition version.

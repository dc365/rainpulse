# ForecastOutput Zarr contract

`ForecastOutput` is the canonical model output consumed by product generation,
publication and verification. Each dataset represents one `run_id`, model
version, configuration version and target grid.

## Dimensions and coordinates

The canonical dimension order for member-dependent forecast fields is
`member × lead_time × lat × lon`.

| Coordinate | Type | Requirements |
|---|---|---|
| `member` | string or integer | Unique member identifiers; deterministic models use one member |
| `lead_time` | `int32` | Minutes after issue time |
| `valid_time` | `datetime64[ns]` | UTC and equal to `issue_time + lead_time` |
| `lat` | `float32` | Identical to the selected `NowcastInput` grid |
| `lon` | `float32` | Identical to the selected `NowcastInput` grid |

Phase 1 deterministic output has one member and exactly 24 lead times:
`5, 10, …, 120` minutes.

## Variables

| Variable | Dimensions | Dtype | Units | Required in Phase 1 |
|---|---|---|---|---|
| `rain_rate` | `[member, lead_time, lat, lon]` | `float32` | `mm h-1` | yes |
| `accum_60` | `[member, lat, lon]` | `float32` | `mm` | yes |
| `accum_120` | `[member, lat, lon]` | `float32` | `mm` | yes |
| `output_valid_mask` | `[lead_time, lat, lon]` | `uint8` | `1` | yes |
| `confidence` | `[lead_time, lat, lon]` | `float32` | `1` | yes |
| `motion_u` | `[lat, lon]` | `float32` | `m s-1` | pySTEPS-LK diagnostic |
| `motion_v` | `[lat, lon]` | `float32` | `m s-1` | pySTEPS-LK diagnostic |
| `prob_gt_1/5/10/20/50` | `[lead_time, lat, lon]` | `float32` | `1` | no; ensemble phase |
| `p10/p50/p90` | `[lead_time, lat, lon]` | `float32` | `mm h-1` | no; ensemble phase |

Probability variables are reserved but must not be published until the
threshold accumulation period, event definition and calibration rules are
frozen in `products.yaml`. A deterministic single-member field must never be
presented as a calibrated probability.

Accumulations use the fixed five-minute integration interval. `accum_60`
integrates lead times 5–60 minutes and `accum_120` integrates lead times
5–120 minutes. Their values are non-negative where valid and `NaN` where the
required output support is invalid.

## Required dataset attributes

| Attribute | Type | Meaning |
|---|---|---|
| `contract_name` | string | Must equal `rainpulse.forecast-output` |
| `contract_version` | string | Must equal `1.1` |
| `run_id` | UUID string | Owning forecast run |
| `job_id` | UUID string | Producing idempotent job |
| `model_id` | string | Stable model family identifier |
| `model_version` | string | Immutable model/artifact version |
| `config_version` | string | Immutable runtime configuration version |
| `input_asset_ids` | array[string] | Raw/standard assets used by the run |
| `issue_time` | string | RFC 3339 UTC issue time |
| `grid_id` | string | Must match the input grid |
| `grid_metric_version` | string | Degree-to-distance conversion used for physical motion fields |
| `runtime_ms` | integer | Non-negative compute runtime |
| `created_at` | string | RFC 3339 UTC publication timestamp |

## Validity and publication rules

- `output_valid_mask` uses exactly `0` and `1`; invalid forecast cells are
  `NaN`, not zero.
- `confidence` is in `[0, 1]` where valid and `NaN` where invalid.
- Every `valid_time` equals the issue time plus its lead time.
- pySTEPS pixel displacement is converted to `motion_u`/`motion_v` with the
  latitude-aware `grid_metric_version`; a constant one-kilometre conversion is
  forbidden for the EPSG:4326 grid.
- A deterministic model uses one member and cannot emit ensemble probability
  or quantile variables.
- Assets are written under `_tmp/{job_id}`, validated and checksummed before an
  atomic publish to `products/{run_id}/{model_id}/{model_version}/`.
- Re-delivery of the same `job_id` must resolve to the same published product,
  never a duplicate product record.

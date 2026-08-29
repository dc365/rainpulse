# ForecastOutput Zarr contract

`ForecastOutput` is the canonical model output consumed by product generation,
publication and verification. Each dataset represents one `run_id`, model
version, configuration version and target grid.

Contract `1.1` remains the Phase-1 deterministic LK format. Contract `1.2` is
the RP-022 additive ensemble format; it does not change or reinterpret any
committed `1.1` artifact.

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

RP-022 ensemble output has at least two members and the same 24 lead times.
Member identifiers are stable integer indices, and the frozen random seed is
recorded in both Zarr attributes and `forecast/summary.json`.

## Variables

| Variable | Dimensions | Dtype | Units | Required in Phase 1 |
|---|---|---|---|---|
| `rain_rate` | `[member, lead_time, lat, lon]` | `float32` | `mm h-1` | yes |
| `member_valid_mask` | `[member, lead_time, lat, lon]` | `uint8` | `1` | required in 1.2 ensemble output |
| `accum_60` | `[member, lat, lon]` | `float32` | `mm` | yes |
| `accum_120` | `[member, lat, lon]` | `float32` | `mm` | yes |
| `output_valid_mask` | `[lead_time, lat, lon]` | `uint8` | `1` | yes |
| `confidence` | `[lead_time, lat, lon]` | `float32` | `1` | yes; technical quality index, not probability |
| `motion_u` | `[lat, lon]` | `float32` | `m s-1` | pySTEPS-LK diagnostic |
| `motion_v` | `[lat, lon]` | `float32` | `m s-1` | pySTEPS-LK diagnostic |
| `motion_valid_mask` | `[lat, lon]` | `uint8` | `1` | optional pySTEPS-LK diagnostic |
| `persistence_rain_rate` | `[lead_time, lat, lon]` | `float32` | `mm h-1` | yes; verification baseline |
| `translation_rain_rate` | `[lead_time, lat, lon]` | `float32` | `mm h-1` | yes; verification baseline |
| `persistence_valid_mask` | `[lead_time, lat, lon]` | `uint8` | `1` | yes; persistence support |
| `translation_valid_mask` | `[lead_time, lat, lon]` | `uint8` | `1` | yes; translation support |
| `prob_gt_1/5/10/20/50` | `[lead_time, lat, lon]` | `float32` | `1` | required in 1.2 ensemble output |
| `p10/p50/p90` | `[lead_time, lat, lon]` | `float32` | `mm h-1` | required in 1.2 ensemble output |

For `1.2`, `prob_gt_1/5/10/20/50` is the raw fraction of members whose
instantaneous `rain_rate` is strictly greater than the named threshold at one
valid time. The thresholds are in `mm h-1`; these variables are not
accumulation probabilities. `p10/p50/p90` are member quantiles of instantaneous
`rain_rate`. The identical semantics are frozen in
`configs/products/rp022-ensemble-products-v1.yaml`.

RP-022 values have
`probability_calibration_status=raw_ensemble_relative_frequency_uncalibrated`.
They may be used for offline evaluation but must not be published as an
operational calibrated probability until the independent Fujian probability
gate is passed. A deterministic single-member field must never be presented as
a probability.

`confidence` is the Phase-1 **technical forecast-quality index** produced from
advected input quality, lead-time decay and low-quality penalties. It is not a
probability that the forecast is correct. Producers using this meaning set
`confidence_kind=technical_forecast_quality_index_not_calibrated_probability`;
product APIs and user interfaces must label it as technical quality rather than
forecast probability or calibrated confidence.

`motion_valid_mask` marks the domain retained for motion-feature qualification
after missing-data boundaries and holes are buffered. It does not replace
`output_valid_mask`: the former diagnoses where optical-flow evidence was
trusted, while the latter remains the authoritative forecast support after the
original observation mask is advected.

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
| `missing_buffer_pixels` | integer | Width excluded from motion-feature qualification |
| `motion_feature_count` | integer | Safe-domain sparse features used to qualify LK |
| `motion_valid_fraction` | number | Fraction of grid retained for motion qualification |
| `motion_fallback_used` | boolean | Whether deterministic motion fallback was used |
| `motion_fallback_reason` | string or null | Structured reason for the fallback |
| `confidence_kind` | string | Declares technical-quality, non-probabilistic meaning |
| `runtime_ms` | integer | Non-negative compute runtime |
| `created_at` | string | RFC 3339 UTC publication timestamp |

Contract `1.2` additionally requires:

| Attribute | Type | Meaning |
|---|---|---|
| `ensemble_member_count` | integer | Number of generated stochastic members, at least two |
| `random_seed` | integer | Frozen uint32 seed used for reproducible generation |
| `input_missing_policy` | string | RP-022 is `reject_any_missing` |
| `output_support_policy` | string | Intersection of deterministic support and finite support of every member |
| `probability_event_operator` | string | `greater_than` |
| `probability_thresholds_mm_h` | array[number] | Exactly `1, 5, 10, 20, 50` |
| `probability_calibration_status` | string | Raw ensemble frequency, not locally calibrated |
| `nominal_pixel_spacing_km` | number | WGS84 geometric-mean cell spacing passed to STEPS perturbations |

The motion and confidence attributes are required for outputs produced by the
RP-016-hardened pySTEPS adapter. The version-1.1 validator remains able to read
older committed RP-014 artifacts that predate these optional diagnostics.

## Validity and publication rules

- `output_valid_mask` uses exactly `0` and `1`; invalid forecast cells are
  `NaN`, not zero.
- `motion_valid_mask`, when present, uses exactly `0` and `1` and has the target
  grid shape.
- `confidence` is in `[0, 1]` where valid and `NaN` where invalid; it is not a
  calibrated forecast probability.
- Every `valid_time` equals the issue time plus its lead time.
- pySTEPS pixel displacement is converted to `motion_u`/`motion_v` with the
  latitude-aware `grid_metric_version`; a constant one-kilometre conversion is
  forbidden for the EPSG:4326 grid.
- A deterministic model uses one member and cannot emit ensemble probability
  or quantile variables.
- An ensemble model must contain at least two members. RP-022 rejects any
  missing input frame. `member_valid_mask` records the finite domain of each
  stochastic member after intersecting it with deterministic advection support;
  `output_valid_mask` is exactly the intersection of all member masks. Member-
  specific edge loss must never become valid no-rain. Member accumulations use
  the corresponding member masks, while probability and quantile products are
  available only on the common output mask.
- Raw probabilities equal the member relative frequency for the frozen strict
  exceedance event, and quantiles must be exactly derivable from `rain_rate`.
- `operational_enabled=false` remains mandatory for the RP-022 product profile;
  this foundation cannot replace the deterministic LK publication path.
- The persistence and whole-field translation arrays are diagnostic baselines,
  not extra ensemble members. All three deterministic paths use the same 24
  lead times, source mask and accumulation convention.
- Missing input support is advected separately from the working precipitation
  copy. Invalid output cells must remain `NaN` and must never become zero rainfall.
- Missing values used only by optical-flow estimation may be filled on a
  temporary working copy, but features inside the configured missing-boundary
  buffer must not qualify the motion field.
- If safe-domain rain pixels or motion features are insufficient, the output
  records a structured fallback reason instead of silently presenting an
  unsupported dense motion field as observed motion.
- Assets are written under `_tmp/{job_id}`, validated and checksummed before an
  atomic publish to `products/{run_id}/{model_id}/{model_version}/`.
- Re-delivery of the same `job_id` must resolve to the same published product,
  never a duplicate product record.

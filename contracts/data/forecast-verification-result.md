# ForecastVerificationResult JSON bundle

`ForecastVerificationResult` is the immutable RP-031 result produced after a
published deterministic `ForecastOutput` can be paired with all 24 future
five-minute `RadarAnalysis` frames on the identical grid.

The bundle has `contract_name=rainpulse.forecast-verification-result` and
`contract_version=1.0`. It contains:

- `summary.json`: run, job, forecast, truth-frame and profile identities plus a
  bounded score summary;
- `metrics.json`: per-model, per-lead, per-threshold physical-kilometre FSS,
  CSI, POD, FAR, MAE, RMSE, bias and coverage evidence;
- `accumulation-metrics.json`: 0 to 1 hour and 0 to 2 hour accumulation scores.

The accepted Phase-1 model set is exactly `lk`, `persistence`, and
`translation`. Metrics use their common valid support. Missing truth or model
cells remain outside that support and are never converted to valid zero rain.
Valid no-rain remains the finite value `0.0` inside `VALID_MASK=1`.

The initial profile uses instantaneous thresholds `0.1/1/5/10/20/50 mm h-1`,
FSS targets `1/5/10/20/40 km`, and accumulation thresholds
`1/5/10/25/50 mm`. The actual odd pixel window chosen for every physical target
is recorded in each row because the grid is geographic.

Completion means that the automatic verification process finished; it is not
a model-promotion decision. `promotion_eligible=false` remains mandatory until
Fujian representative cases and the business acceptance gate are frozen.

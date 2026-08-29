# EnsembleApplicationProductBundle contract

`rainpulse.ensemble-application-product-bundle contract_version=1.0` is the
RP-023 offline distribution boundary for one committed ensemble
`ForecastOutput contract_version=1.2`.

It does not enter the deterministic RP-015 product database, product events or
`product-published` workflow. The bundle remains file-backed and read-only
until the independent Fujian probabilistic acceptance gate passes.

## Frozen layer suite

One bundle contains eight layer identities, each with 24 valid times at
five-minute steps from T+5 through T+120:

- instantaneous rain-rate exceedance probabilities for strict events
  `>1`, `>5`, `>10`, `>20` and `>50 mm h-1`;
- instantaneous rain-rate member quantiles P10, P50 and P90.

Each layer/lead has two derived assets:

- a transparent north-up RGBA PNG for the OpenLayers GIS;
- a point-registered NetCDF3 classic field on the canonical EPSG:4326 grid.

Probability NetCDF values use unit `1`; quantile NetCDF values use `mm h-1`.
The PNG is a display derivative only. Scientific processing and exchange use
the source ForecastOutput or NetCDF, never pixels sampled from the PNG.

## Probability boundary

Exceedance values are raw member relative frequencies with
`event_operator=greater_than`. They are not locally calibrated probabilities.
Every manifest therefore requires:

- `calibration_status=raw_ensemble_relative_frequency_uncalibrated`;
- `operational_eligible=false`;
- `operational_gate=independent_fujian_probabilistic_acceptance_required`.

The API and Web must expose the same offline and uncalibrated boundary. Merely
copying a bundle into the read-only report directory does not publish a
business product.

## Grid, missing data and integrity

The Phase-1 grid remains
`fuzhou_118_123_25_27_0p01deg_v1`, with 201 latitude points, 501 longitude
points and pixel-edge bounds `[117.995, 24.995, 123.005, 27.005]`.

All layers use the ensemble ForecastOutput common `output_valid_mask`, which is
the intersection of finite support across members. Missing cells remain NaN in
the source and become NetCDF `_FillValue=-9999.0` only at the application
boundary. Valid zero probability and valid zero rain rate remain numeric zero.
Transparent PNG pixels are not sufficient evidence of state, so every asset
records valid/missing counts and coverage ratio.

The manifest registers every relative object path, asset ID, media type,
SHA-256, size, lead time, valid time, unit and rendering legend. The Go reader
accepts only manifest-listed assets and validates path containment, size,
checksum and file signature before returning bytes.

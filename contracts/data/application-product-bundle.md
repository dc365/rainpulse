# ApplicationProductBundle contract

`ApplicationProductBundle contract_version=1.0` is the RP-015 atomic
distribution boundary. It is derived from one committed
`ForecastOutput contract_version=1.1`; no rendered or exchange artifact may be
used as the input of another scientific processing stage.

One build publishes exactly three immutable product identities:

- `rain_rate`: 24 valid times at 5-minute steps from T+5 through T+120.
- `accumulation_60`: one amount field valid at T+60, integrated over `(T,T+60]`.
- `accumulation_120`: one amount field valid at T+120, integrated over `(T,T+120]`.

Every rain-rate lead and each accumulation contains a browser RGBA PNG, a
WGS84 Cloud Optimized GeoTIFF, and one NetCDF classic application file. The
rain-rate product also contains a fixed-record point-query index. The index is
an API delivery aid only; it does not replace ForecastOutput or NetCDF.

## Grid and state rules

Phase 1 is fixed to `fuzhou_118_123_25_27_0p01deg_v1`, point-centre
registration, `lat=201`, `lon=501`, and 0.01 degree spacing. Scientific arrays
are `lat × lon` with increasing coordinates. PNG and COG rows are explicitly
flipped north-up and use pixel-edge bounds
`[117.995, 24.995, 123.005, 27.005]`.

Valid no-rain remains numeric `0.0` in COG, NetCDF and the point index. Missing
coverage remains `NaN` in ForecastOutput, becomes nodata or `_FillValue` only
at distribution boundaries, and must never be converted to valid zero.
Transparent PNG pixels alone are ambiguous, so every layer manifest records
valid, missing and no-rain cell counts plus coverage ratio.

## Atomic manifest

The bundle manifest records run, job, model-run, model, config, grid and source
ForecastOutput identities, product IDs, valid times and all object paths. Each
asset entry includes media type, SHA-256, size, lead time, valid time, units,
coverage ratio and cell-state counts. Object paths are safe relative paths;
the object-store `_SUCCESS.json` marker is written last.

The canonical bundle URI is:

`s3://rainpulse/products/{run_id}/{model_id}/{model_version}/distribution/{product_config_version}/application-products`

REST and events carry only registered identities, summaries and object URIs.
They never carry meteorological grids.

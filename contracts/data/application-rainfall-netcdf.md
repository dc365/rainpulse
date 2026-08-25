# ApplicationRainfallNetCDF contract

`ApplicationRainfallNetCDF` is a distribution artifact derived from an
accepted `RadarAnalysis` or `ForecastOutput`. It is not an internal processing
format and must never become the source for another RainPulse analysis step.

## File profile

- NetCDF classic, one valid time and one precipitation element per file.
- Dimension order is `lat × lon`.
- `lat` and `lon` are `float32`, strictly increasing, and byte-for-byte equal to
  the registered grid coordinates after serialization.
- Phase 1 uses `fuzhou_118_123_25_27_0p01deg_v1`, with `lat=201` and `lon=501`.
- A `rain_rate(lat, lon)` file uses `float32` and `mm h-1`.
- A `rainfall_amount(lat, lon)` file uses `float32` and `mm` and records the
  accumulation interval. Rate and amount are never mixed in one element.
- `_FillValue=-9999.0`; valid no-rain is `0.0`. Internal `NaN` is converted to
  the fill value only while exporting this artifact.

The coordinate variables use CF `standard_name`, `long_name`, `units` and
`axis` attributes. The rainfall variable uses an applicable CF standard name,
unit, `coordinates`, and `grid_mapping` metadata. Global attributes include
`Conventions`, `grid_id`, source product/run/analysis identity, issue/valid UTC
times, lead time, model/QPE/config versions, creation time, and checksum.

For compatibility with the supplied numerical-model example, exporters also
write the registered legacy attribute profile (`DataTime`, `ElementCode`,
`StartLon`, `EndLon`, `StartLat`, `EndLat`, coordinate intervals and counts).
Those attributes are generated from the grid and product identity; stale or
contradictory copied metadata is invalid.

The first application profile contains only coordinates, one rainfall field,
and CRS metadata. QI, flags, source radar, and diagnostics remain in canonical
Zarr artifacts and APIs.

Publication is atomic under
`products/application-netcdf/{grid_id}/{product_type}/{yyyy}/{mm}/{dd}/{valid_time}/`.

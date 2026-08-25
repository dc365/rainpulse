# RenderedRainfallLayer contract

`RenderedRainfallLayer` is a browser-display artifact derived directly from the
same accepted `RadarAnalysis` or `ForecastOutput` used for application NetCDF.
It is never an authoritative scientific data source.

Phase 1 publishes one lossless RGBA PNG per valid time/lead plus a JSON layer
manifest. For `fuzhou_118_123_25_27_0p01deg_v1`, the PNG is exactly `501 × 201`
pixels. Scientific arrays use south-to-north latitude order; PNG row zero is the
northernmost row, so rendering performs one explicit vertical flip.

The manifest records `grid_id`, source product identity, width, height,
coordinate-centre bounds, pixel-edge bounds, issue/valid time, lead time,
product type/unit, palette version, value breaks, opacity rule, coverage ratio,
PNG checksum, and creation time. The Phase 1 pixel-edge bounds are
`[117.995, 24.995, 123.005, 27.005]`; using coordinate-centre bounds as image
edges is a half-pixel alignment error.

Valid no-rain may be visually transparent in the main rainfall layer. Missing
or rejected coverage also uses zero alpha there, but its distinct scientific
state remains in Zarr/NetCDF and must be exposed by coverage status or a
separate diagnostic layer. Rendering must never turn missing into valid zero.

Publication is atomic under
`products/rendered/{grid_id}/{product_type}/{yyyy}/{mm}/{dd}/{valid_time}/`.

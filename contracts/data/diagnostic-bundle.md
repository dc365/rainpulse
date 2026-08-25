# DiagnosticBundle v1.0

`DiagnosticBundle` is the RP-012 application-rendering product derived by a
Python Worker from one immutable `RadarAnalysis` and the exact contributing
`QCRadarVolume` assets. React and Go must not parse Zarr arrays.

The atomically published bundle contains `manifest.json` and transparent PNG
layers. Grid images use the exact RadarAnalysis point-centre grid, are north-up,
and carry the frozen pixel-edge bounds. Polar images use the lowest sweep that
contains DBZH and record its elevation and maximum range; they are diagnostic
PPI views, not EPSG:4326 map layers.

Required grid layers are `DBZH_QC`, `RATE_QPE`, `QUALITY_INDEX`,
`SOURCE_RADAR`, `BEAM_HEIGHT`, `QC_FLAGS`, and a three-state mask. Required
per-radar polar layers are `DBZH_RAW`, `DBZH_QC`, `QUALITY_INDEX`, and
`QC_FLAGS`.

Missing pixels have alpha 0. Valid no-rain is not transparent. Low-quality pixels remain visible
and are identified by the state-mask layer. A flag image
is a diagnostic priority rendering of the immutable uint32 bit set; the
underlying flag value remains authoritative. PNG colors never replace physical
values or masks and must identify their palette/config/renderer versions.

Go exposes the validated manifest and proxies only paths listed in it. Layer
paths are never accepted as arbitrary object-store keys. A bundle is written
under a renderer-version-isolated prefix and `_SUCCESS.json` is committed last.

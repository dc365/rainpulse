# RainPulse unified workspace design

## Product surface

RainPulse now has two routes only:

- `/`: the operational and engineering evidence workspace.
- `/admin`: read-only data-source, pipeline, alert, and failure evidence.

The main workspace owns one cycle selector, one absolute-valid-time timeline,
and one synchronized map view. Quality control, nowcasting, and verification are
presets of the same workspace rather than separate pages.

## Main workspace hierarchy

1. Compact header: product identity, live-follow/history state, issue cycle,
   freshness, and the Admin entry.
2. Preset strip: forecast comparison, QC investigation, or verification replay.
3. One to four maps sharing the same OpenLayers `View` and raster palette.
4. One timeline based on `valid_time`, not model-specific lead indices.
5. Detailed provenance stays in APIs/Admin; the map title shows only lifecycle,
   model, and native cadence.

Stable forecast slots are Radar QPE, pySTEPS-LK, STEPS, and NowcastNet.
Historical cycles append later Radar QPE analyses to the same absolute timeline,
so the QPE slot becomes synchronized verification truth at forecast valid times. A
missing model output keeps its slot and states the reason. It is never replaced
by another model and is never interpolated to a cadence the model did not
produce.

## QC preset

The selected radar uses four synchronized slots where evidence exists:

- raw polar reflectivity;
- QC reflectivity;
- gridded QC flags;
- final radar QPE.

This layout is intentionally optimized for checking whether a removed echo was
non-meteorological and whether that removal changed the downstream mosaic.

## Visual rules

- The raster is the primary evidence, not decorative cards.
- Borders and background steps provide hierarchy; avoid gradients and shadows.
- Missing coverage remains transparent and distinct from valid no-rain.
- Engineering/shadow/offline lifecycle is always visible.
- CST is the operator-facing timezone; UTC remains visible and authoritative.
- Desktop shows two-by-two synchronized maps. Mobile shows one map at a time
  with panel tabs while retaining the same cycle and timeline.
- OpenLayers remains the only GIS runtime and the local GSHHG coastline remains
  available when the XYZ basemap is unavailable.

## Data contract

The browser consumes the UI-oriented projection:

- `GET /api/v1/workspace/cycles`
- `GET /api/v1/workspace/cycles/{cycle_id}`
- `GET /api/v1/workspace/ingest-status`
- `GET /api/v1/workspace/nowcastnet-shadow-status`

The projection composes existing bounded domain APIs inside the Go control
process. React does not join run, analysis, product, diagnostic, and ensemble
catalogs itself and never reads Zarr or object storage directly.

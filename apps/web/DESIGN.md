# RainPulse unified workspace design

## Product surface

RainPulse now has three routes only:

- `/`: the operational and engineering evidence workspace.
- `/dataflow`: the realtime radar-processing dataflow screen (no admin
  credentials; it reads only workspace projections).
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

## Dataflow screen (`/dataflow`)

The dataflow screen answers, on one wall-clock canvas, what already finished,
what is running right now, and how each radar's quality-control chain behaved:

1. Verdict banner: overall chain state, participating radars, current analysis
   cycle, snapshot age.
2. Chain beat strip: the seven downstream nodes (数据到达 → 解码 → 极坐标质控 →
   格点化 → 拼图·QPE → 临近预报 → 产品发布) with per-window completed/running/
   queued/failed counts and a p50 runtime; connectors animate only while the
   chain is live.
3. Radar swimlanes: one row per radar plus shared 分析周期 and 预报与发布 rows.
   Each volume scan is one block subdivided into stage segments positioned by
   the jobs' own started/finished timestamps; left of the "现在" line is
   history, the head itself is live. Silence longer than the six-minute beat
   renders as a 断流 marker. Blocks open an evidence drawer (per-stage queue
   and compute time, failure codes, scan quality).
4. Radar status strip: per-radar health, data delay, scan completeness, mean
   QI, latest QC runtime and ingest provenance.
5. Event ticker: at most 30 terminal events derived from the same window rows.

Visual rules follow the workspace tokens: borders and background steps only,
motion is confined to live-processing pulses, the flowing connector dots and
the advancing now-line, and `prefers-reduced-motion` disables all of it. The
axis labels CST while every machine-readable timestamp stays UTC.

## Data contract

The browser consumes the UI-oriented projection:

- `GET /api/v1/workspace/cycles`
- `GET /api/v1/workspace/cycles/{cycle_id}`
- `GET /api/v1/workspace/ingest-status`
- `GET /api/v1/workspace/dataflow?window=30|60|180`
- `GET /api/v1/workspace/dataflow/events` (SSE, `dataflow.changed` revision ping)
- `GET /api/v1/workspace/nowcastnet-shadow-status`

The projection composes existing bounded domain APIs inside the Go control
process. React does not join run, analysis, product, diagnostic, and ensemble
catalogs itself and never reads Zarr or object storage directly. The dataflow
payload shape is frozen in `contracts/schemas/dataflow-snapshot-v1.schema.json`.

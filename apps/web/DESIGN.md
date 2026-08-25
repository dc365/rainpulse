# RainPulse web design direction

## 1. Visual theme and atmosphere

RainPulse is a restrained scientific operations console for forecasters and
duty staff. The real georeferenced rainfall layer is the visual anchor; status,
quality and provenance stay dense, quiet and immediately scannable.

## 2. Color palette and roles

- `canvas`: `oklch(94.5% 0.007 165)`, page background.
- `paper`: `oklch(98.5% 0.004 165)`, primary work surfaces.
- `ink`: `oklch(25% 0.018 165)`, primary text.
- `muted`: `oklch(52% 0.014 165)`, secondary text.
- `teal`: `oklch(50% 0.092 175)`, operational selection and healthy state.
- `risk`: `oklch(53% 0.12 48)`, degraded state and warnings.
- Rainfall colors follow the frozen `rainfall-operational-v1` product palette.

## 3. Typography rules

Use the existing system stack, led by Apple system Latin and PingFang SC for
Chinese. Operational numbers use tabular figures. Headlines remain compact;
long Chinese descriptions use a line height of at least 1.65.

## 4. Component styling

Buttons use the existing 7 px control radius and a visible focus outline.
Panels are mostly flush sections separated by one-pixel dividers. Product tabs,
timeline frames and map selections use background steps instead of decorative
shadows. Pressed controls scale to 0.97.

## 5. Layout principles

The desktop forecast workspace uses a wide map and a narrow evidence rail.
Status is placed above the map; the five-minute timeline sits immediately below
it. Point and area results stay adjacent to the map so the operator does not
lose geographic context.

## 6. Depth and elevation

Depth is communicated with `canvas`, `paper` and elevated white background
steps. Borders remain structural dividers. The rainfall PNG is an OpenLayers
`ImageStatic` layer above a configurable XYZ basemap, local coastline and
latitude/longitude graticule, without glass effects.

## 7. Guardrails

- Never hide missing-data semantics behind zero rainfall.
- Never imply that publication status proves meteorological skill.
- Do not expose internal Zarr artifacts to the browser.
- Keep OpenLayers as the single GIS runtime; do not add a second map framework.
- Preserve the product pixel-edge extent separately from the point-centre grid.
- Keep the local GSHHG coastline available when the XYZ basemap is unavailable.
- Keep UTC explicit on every forecast time.
- Preserve product, model, configuration and source SHA provenance.
- Avoid decorative gradients, generic card grids and ornamental animation.

## 8. Responsive behavior

At 980 px the evidence rail moves below the map. At 700 px status metrics and
query panels become single-column, map controls condense without covering the
valid-time or legend overlays, and the 24-frame timeline scrolls horizontally.
Controls retain a minimum 40 px hit area down to 375 px.

## 9. Agent prompt guide

- Build a forecast status strip on `paper` with 11 px labels, 24 px tabular
  values, one-pixel structural dividers and no decorative shadow.
- Build an EPSG:4326 OpenLayers workspace with a configurable XYZ basemap,
  GSHHG fallback, graticule, scale line, city labels and the 501x201 product PNG
  at pixel-edge extent `117.995,24.995,123.005,27.005`.
- Build a 24-frame timeline using 40 px minimum targets, previous/play/next
  controls, 900 ms playback, 0–1h/1–2h bands, teal selected state and UTC labels.
- Build a point forecast line chart using the product teal, subtle horizontal
  guides and tabular rain-rate values; do not use bars for the time series.

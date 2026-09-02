# RainPulse interface system

## Direction

RainPulse is a dense operational workspace for forecasters who must compare live radar analysis, short-term forecasts, model readiness, and evidence without losing temporal context. The interface should feel calm, precise, and instrument-like: information-rich without resembling a generic card dashboard.

Domain concepts that shape the UI are radar sweeps, rainfall fields, issue and valid times, forecast lead, synchronized model panels, quality evidence, and operational shadow status. The visual world comes from weather radar rooms: pale map gray-green, paper white, radar teal, restrained slate text, muted grid lines, and a single burnt-orange risk color.

## Foundations

- Depth: borders-only with quiet surface shifts. Do not introduce decorative shadows or gradients.
- Surfaces: `--rp-canvas` is the base; `--rp-paper` is the primary working surface; white is reserved for inset controls and focused data panes.
- Color: `--rp-teal` marks the current time, ready state, and primary action. `--rp-risk` is reserved for warnings or unavailable operational evidence. Neutral structure uses `--rp-ink`, `--rp-muted`, and `--rp-line`.
- Spacing: 4 px base unit. Prefer 4, 8, 12, 16, and 24 px intervals; use smaller optical adjustments only inside dense time or map controls.
- Typography: Chinese-first labels with compact tabular numerals for times, lead minutes, and measurements. Primary operating text should remain in the 11–14 px range; metadata may use 9–10 px only when paired with a clear larger value.
- Radius: 6 px for controls and compact panels; timeline tracks and structural separators remain square or nearly square.

## Signature pattern: synchronized forecast time rail

Use a continuous rail for any sequence of forecast-valid times. Do not render every timestamp as an independent bordered card.

- Show T0, hour boundaries, and the terminal lead as major ticks.
- Mark the selected lead with a radar-teal cursor, compact lead label, and an unambiguous `aria-current` state.
- Keep issue time, valid time, frame count, and interval visible around the rail.
- Keep playback, hour context, frame state, issue time, and valid time in one compact metadata band: playback at the far left, hour context centered on the +60 min boundary, and time/state at the far right. Do not add a separate playback toolbar above it.
- On wide screens, cap each lead-time cell at about 72 px and center the continuous rail so 5-minute steps remain compact instead of stretching across the viewport.
- Align 0–1 h and 1–2 h context bands exactly with the +60 min boundary.
- At 1025–1800 px, place the two hour-context labels immediately on either side of the +60 boundary so the right status block cannot occlude them. At 1024 px and below, retain the boundary but omit these auxiliary labels; the lead ticks remain authoritative.
- Encode per-model frame availability as tiny ordered dots beneath each tick; preserve the same model order as the map panels and footer legend.
- Auto-scroll the selected lead into view on narrow screens.
- Support previous/next controls, playback, Left/Right arrows, Home, and End.
- Honor reduced-motion preferences and provide hover, focus, active, disabled, playing, missing-data, and end-of-range states.

## Reusable workspace patterns

- Synchronized comparison maps lead the page; controls and evidence remain visually subordinate.
- Desktop opens in four-map comparison mode. Enter single-map focus only through an explicit algorithm choice or a panel's compact focus control; never infer QPE or another default algorithm. Focus preserves cycle, valid time, playback, shared map view, and layer settings, and `Esc` returns to comparison.
- Persist focus only as `layout=single&panel=<panel_id>` in the URL. With no valid focus parameters, render comparison mode. Mobile continues to use its existing single-panel tabs.
- Use Chinese as the primary label language while retaining established algorithm names such as QPE, pySTEPS, and NowcastNet.
- Prefer thin separators, aligned data columns, and compact status markers over nested cards.
- Keep warning color semantic. Model identity is expressed by fixed order and labels, not a decorative multicolor palette.
- At mobile widths, stack metadata and playback controls, keep map panels readable, and allow only the internal timeline rail or legend to scroll horizontally—never the page itself.
- On desktop, the primary workspace occupies one dynamic viewport: fixed-height controls, a flexible 2×2 synchronized map grid, and the timeline visible at the bottom. Map roots must fill their allocated grid cell instead of imposing a fixed viewport height.
- At desktop viewport heights of 900 px or less, compact spacing and controls while preserving 11–14 px operational text. If the viewport becomes too small to keep maps readable, allow document scrolling below the 640 px minimum rather than shrinking map content further.
- Comparison-map raster products default to 100% opacity and must reproduce the source product palette exactly. Do not use saturation or contrast filters to disguise lifecycle or opacity bugs.
- Raster display separates base rendering from annotation: `格点 / 平滑` is one compact exclusive switch, while `点值` is an independent overlay toggle that works with either base style. Grid is the operational default; point labels remain zoom-adaptive.
- When the pointer is over a valued raster pixel, show a compact translucent probe beside it with WGS84 longitude/latitude and only the published palette value—no interval or unit. Transparent or unmatched pixels show no probe and no point label.
- Comparison maps have no dedicated header row. Put the 12 px algorithm name and 9 px role/lifecycle/time metadata in a single compact caption over the map's top-left corner, using a quiet translucent paper surface and no shadow so the saved height belongs to the map.
- Every comparison map with legend metadata shows its own compact legend inside the bottom-left corner: about 310 px wide and 20 px high, product-native colors, only the unit is shown before continuous scales (omit the redundant “图例” label), 7–8 px threshold labels, a 72%-opaque paper background, no shadow, and internal horizontal scrolling on narrow panels. Keep it clear of the top-left caption and right-side scale/attribution controls.
- The operational workspace opens with the restrained gray-green basemap visible. Keep its color treatment subordinate to the 100%-opacity precipitation raster, and retain the explicit “底图” toggle.

## Avoid

- Timestamp button grids, repeated filters, duplicate time axes, decorative gradients, floating card shadows, oversized status badges, or arbitrary algorithm colors.
- Dense metadata that competes with the maps or obscures the selected valid time.
- Page-level horizontal overflow at 390 px and narrower.

# Verification analysis v1

The existing single-frame API remains unchanged. New analysis compares a selected
algorithm set on exactly the same observed/forecast finite nonnegative grid cells.
All selected algorithms must have native frames; missing members skip the pair,
never silently shrink the comparison set. Shared support may vary by lead/cycle.

POST `/api/v1/workspace/verification/compare`: `cycle_id`, `algorithms` (unique
lk/steps/nowcastnet), `lead_minutes` (5..120 by 5), `psd` boolean. Returns identity,
status/reason, source fingerprint and per-algorithm metrics. PSD is optional and
does not depend on thresholds or neighborhood settings.

PSD: pick the largest all-valid common square among 256/128/64/32 cells, choosing
the first row-major candidate, independent of rain intensity. If none exists,
report insufficient common support. Return its bounds/shape/coverage. Subtract
each field's mean, apply the same separable Hann window and compute two-sided 2D
periodogram density `abs(fft2)**2 * dx*dy / sum(window**2)`. Return radial-bin mean
density versus reciprocal radial frequency (km), using complete annuli up to the
smaller axis Nyquist frequency. Density units are `(mm/h)^2 km^2`; no dry filling,
no interpolation, no ranking, no comparison of different crop domains. Zero
spectral power remains zero, not an artificial positive floor.

POST `/api/v1/workspace/verification/jobs`: `cycle_ids` (1..128 unique catalog IDs),
`algorithms`, `leads` (1..24 unique native candidate leads), `threshold` and
`window_km` (existing allowed settings). Job settings are frozen and echoed.
GET `/api/v1/workspace/verification/jobs` lists up to 8 retained job summaries;
GET `.../jobs/{id}` returns progress, per-cycle/lead compact records and final summary;
POST `.../jobs/{id}/cancel` cancels cooperatively. Only one task runs at a time;
competing requests get 409 with the active job ID. ID is a hash of the request,
so repeating a request replaces its result, not a new selectable version.
Jobs last at most 2 hours. Each source projection is frozen for one cycle; the
source fingerprint is retained per record. Successful scores with zero coverage
remain explicitly unscorable. Partial failures/skips are counted, not zero scored.

Python summarizes successful matched records with equal cycle/lead weight (macro
mean, not pooled contingency CSI). A score enters comparative means only when all
selected algorithms have a finite value for it in that record. Return effective N
per metric and by lead, plus coverage. These are radar-QPE engineering statistics,
not independent cases or formal significance tests. Same event overlapping issue
times must not be claimed as independent samples.

Only compact JSON reports persist under `RAINPULSE_VERIFICATION_JOB_ROOT`, bounded
to 8 files. Save atomically after each cycle and at completion. Restarted active
tasks become interrupted (manual restart), never silently report completion.
No arrays, model jobs, new forecast versions or GPU tasks. GET responses use
no-store. UI reports captured settings and calculation time, not an implicit claim
that saved scores reflect later regenerated products. Explicit rerun refreshes them.

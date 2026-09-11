# Workspace on-demand interval accumulation v1

React calls the Go workspace API; Go resolves current product provenance and sends only
URIs, SHA256 identities, lead indices and grid metadata to the existing product-builder
worker. No numerical arrays cross REST and no model/GPU job is submitted.

`POST /api/v1/workspace/accumulations`

```json
{"cycle_id":"<catalog cycle ID>","algorithm":"lk","start_minutes":15,"end_minutes":45}
```

Allowed algorithms: `qpe`, `lk`, `steps`, `nowcastnet`. Required start/end are integer
multiples of 5 with `0 <= start < end <= 120`. Unknown fields and client source URIs
are rejected. Four maps request independently, so unavailable STEPS/NowcastNet does
not suppress available QPE/LK. The response echoes cycle/start/end and contains one
standard workspace panel with `data_kind=accumulation_interval`, unit `mm` and a single
frame at the interval end. Missing inputs return an unavailable panel, never a rate
image or an old precomputed accumulation. Invalid requests return 400.

Each right-endpoint rate R(t), t=start+5,...,end, contributes R(t)*5/60 mm.
Every contributing cell must be valid. Missing is not dry. STEPS integrates each
member first, then computes P50 with complete member support. NowcastNet integrates
the existing ensemble mean (a linear operation on its retained common-support mean);
this is not an ensemble accumulation percentile. QPE uses the future observed frames,
not T0 or a forecast substitute.

`GET /api/v1/workspace/accumulations/<64-hex-key>/image` returns north-up RGBA PNG.
The existing `/api/v1/workspace/sample?asset_url=...&longitude=...&latitude=...`
returns the exact accumulated cell in mm, using the same cached array.

Worker cache identity includes algorithm, interval, source URIs/checksums/lead mapping,
issue time and bounds. Results are process-local, expire after 10 minutes and are
bounded to 32 entries / 64 MiB. Point-source bytes are separately bounded to 64 MiB;
ensemble source artifacts are validated and bounded to 512 MiB compressed. One
heavy computation executes at a time, at most four HTTP operations queue; overflow
returns 429. PNG/sample cache misses return 410 (recalculate), never a stale fallback.
Browser/API responses use `Cache-Control: no-store`. The active view refreshes its
ephemeral results before expiry. No NetCDF, persistent versions or database product
rows are generated. Existing export/offline bundles are unchanged.

Worker endpoint is internal-only on the existing health listener (product-builder
profile only). Optional Go setting `RAINPULSE_INTERVAL_WORKER_URL` defaults to
`http://product-builder-worker:8091`. Browser access is always through Go.

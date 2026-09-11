# Unified Workspace projection 1.0

The workspace projection is a read-only browser-facing contract layered over
RainPulse's domain OpenAPI. It intentionally keeps UI composition out of React
without changing the authoritative radar, analysis, product, and verification
contracts.

## Routes

```text
GET /api/v1/workspace/cycles
GET /api/v1/workspace/cycles/{cycle_id}
GET /api/v1/workspace/ingest-status
GET /api/v1/workspace/nowcastnet-shadow-status
```

`cycles` and cycle detail responses carry `schema_version: "1.0"`. A cycle is
identified by one grid and one UTC issue time. Its detail contains one absolute
`valid_time` timeline and an ordered list of map panels.

The cycle list accepts `limit` (default 100, maximum 500) and `cursor` (the
previous page's `next_cursor` cycle ID). Items are ordered by UTC issue time
descending, then cycle ID descending. `next_cursor` is absent on the final page.
Clients must follow all pages before presenting a complete history catalog.
Upstream analysis versions are also paginated before per-cycle selection, so
recomputing one cycle cannot evict older times. Pagination is a live read, not a
frozen snapshot; a later refresh discovers concurrently added newer results.
Date/time filtering uses Beijing time in the picker and only filters published
results. It neither scans raw directories nor launches processing; an empty
result range makes no claim about raw-data availability.

## Stable forecast panels

The first four panel identities are stable even when products are absent:

1. `qpe` — radar QPE observation/truth;
2. `lk` — pySTEPS-LK;
3. `steps` — pySTEPS-STEPS;
4. `nowcastnet` — Fujian NowcastNet shadow.

Missing products keep their slot with `status` and `unavailable_reason`. A
client must never substitute another algorithm's frame or interpolate a model
onto an unsupported cadence. For historical cycles, later RadarAnalysis QPE
frames through +120 minutes are appended to `qpe` as verification truth.

## Safety semantics

- `missing` radar coverage is never represented as valid no-rain.
- `realtime_shadow`, `offline`, `analysis`, and `operational` lifecycle labels
  remain visible to the client.
- NowcastNet probe status is informational and cannot grant publication or
  operational eligibility.
- The status proxy endpoints fail with HTTP 503 and a machine-readable reason
  when their internal service is not configured or unavailable.

## Prelaunch client consistency and verification

The client keeps the requested cycle separate from the committed displayed
snapshot. Header, valid time and map descriptors switch together after a valid
cycle response. A failed selection keeps the old cycle identity visible.
Following realtime is operator intent and does not expire when radar data is
stale. Cached/stale responses must not be presented as fresh.

`frame_kind`, when supplied, is `analysis`, `native` or `derived`; derivation and
source leads remain attached to derived frames. Point verification accepts only
explicit native forecasts and native/analysis truth at the same valid time,
coordinate and rain-rate units. Missing, derived or mismatched pairs are not
scored. The workbench point comparison is N=1 against radar QPE, not a full-field
skill score or an independent gauge validation.

`GET /api/v1/workspace/events` emits `workspace.changed` with a stable `revision`.
Clock-only freshness changes do not alter that revision. SSE sends heartbeat
comments. One bounded producer per API process uses the normal projection
caches; browser reconnection and low-frequency polling recover missed events.

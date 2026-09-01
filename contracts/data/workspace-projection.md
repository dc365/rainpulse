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

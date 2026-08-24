# RawRadarAsset metadata contract

`RawRadarAsset` registers one immutable radar base-data object. The control
plane records this metadata and publishes only the object URI; the raw bytes
never pass through REST, NATS, or PostgreSQL.

## Required fields

| Field | Type | Requirements |
|---|---|---|
| `asset_id` | UUID | Stable idempotency identity for this exact object |
| `radar_id` | string | References one versioned radar configuration |
| `source_uri` | URI | Read-only archived object, not an arrival-directory path |
| `source_format` | string | Verified decoder-adapter identifier |
| `source_format_version` | string or null | Null only when the source format has no version |
| `sha256` | string | Lowercase 64-character SHA-256 of the raw bytes |
| `file_size_bytes` | integer | Non-negative raw size |
| `volume_start_time` | datetime | RFC 3339 UTC |
| `volume_end_time` | datetime | RFC 3339 UTC, not earlier than start |
| `received_at` | datetime | RFC 3339 UTC arrival time |
| `source_version` | string | Version of source registration/delivery configuration |
| `radar_config_version` | string | Immutable radar configuration used to identify the asset |

Optional discovery metadata such as original filename and delivery endpoint
may be stored in a small metadata object. It must not include credentials,
decoded arrays, or guessed scan geometry.

## Identity and archive rules

- The deduplication key is `(radar_id, sha256)`; redelivery returns the same
  asset instead of creating another raw object.
- Archive keys use
  `radar/raw/{radar_id}/{yyyy}/{mm}/{dd}/{scan_time}/{sha256}/{filename}`.
- Raw objects are never overwritten or modified by decode/QC jobs.
- A raw asset may be rejected or quarantined, but its quality state must not be
  represented as valid no-rain.
- Retention is a separate, explicit policy. No ingest or worker task deletes a
  raw object as part of normal processing.

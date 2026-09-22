# Immutable asset read sessions v1

Scope: Python internal API; no public OpenAPI endpoint or output-asset schema changes.

`ArtifactObjectReader(client, max_size_bytes=None, max_workers=None)` retains its
existing full-read signature, including `load(artifact_uri=...)`. Full reads
return a fresh `dict[str, bytes]` of every logical object. Byte values may be
reused because they are immutable; callers cannot mutate cached content.

New methods:

```python
reader.load_selected(artifact_uri, keys=["qc/summary.json"])
reader.load_selected(artifact_uri, keys=[".zgroup", ".zattrs"],
                     prefixes=["rain_rate", "member_valid_mask", "lat", "lon", "lead_time"],
                     expected_sha256=committed_forecast_hash)
session = reader.open(artifact_uri, expected_sha256=committed_hash)
objects = session.load(keys=["qc/summary.json"])
```

A session pins one validated success-marker snapshot. New sessions always fetch
`_SUCCESS.json`; absent/corrupt/changed markers are not hidden by the object cache.
Keys and prefixes are exact relative paths; prefixes match a complete path
component, not an arbitrary textual prefix. Missing keys/prefixes raise errors;
an empty selection is rejected. No missing object becomes an empty/zero array.

Schemas 1/2: validate all manifest metadata and reproduce the whole-artifact hash
from sorted logical key/length/content-hash entries. Download and verify only
selected objects. This binds selected bytes to the complete declared asset
identity; it is NOT an audit of the current physical presence of every unselected
object. The optional expected hash binds the manifest to the caller's committed
asset identity. Without it, the reader retains the existing trusted-marker boundary.

Schema 3: existing packed assets have physical-pack hashes but no logical-object
hashes. Read/verify all physical packs and reconstruct the entire logical digest
using memoryviews; only then copy selected objects. Do not describe this fallback
as range I/O or as a new storage format. Atomic publication and marker schemas do
not change in this batch.

Cache identity is namespace + bucket + immutable object path + SHA-256 + byte
length. The normal MinIO factory attaches an endpoint/principal-scoped opaque
namespace. Unknown externally provided clients do not share entries by default.
There is no persistent disk cache, Redis, or cross-process cache. TTL, maximum
bytes, maximum entries, maximum object size, and in-flight object download count
are independently bounded. A cache miss or integrity failure cannot be converted
into a successful meteorological result. TTL limits reuse, not the age of weather
data; time eligibility remains the existing domain pipeline's responsibility.

Metrics with `asset_cache_` prefix are **process totals**, not per-job deltas.
Health exposes the same budget/counters. `last_session.stats` reports selected
object counts, downloads and schema-3 fallback for a specific reader session.

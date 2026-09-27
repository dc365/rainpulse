# C/D execution-only invariants (2026-09-27)

Baseline: `4c87cc83dc0036b303d904c108d292894dd39f28`. No change to QC thresholds, observation semantics, candidate eligibility, source ordering or publication protocol.

## C: bounded height state

`layer_memory_bytes` is one aggregate retained-array budget for the SX/S/X height states, not a per-workspace allowance. Only one tile can be borrowed at a time. If the budget is smaller than one tile, an explicit single transient tile is allowed, bounded by the existing `maximum_tile_bytes`; peak tracked height arrays are no larger than `max(layer_memory_bytes, tile_bytes)`. This is not an RSS cap: decoded cuts, outputs, geometry, allocator overhead and OS file cache are additional resources.

When all height states fit, no scratch state files are written. Otherwise a shared LRU stores dirty tiles; an evicted dirty tile is written once to a private file using a bounded memoryview and atomic rename. Worst-case reserved scratch includes every height-state byte and one temporary replacement tile. The source-staging reservation uses the same aggregate calculation. No scratch is published. All output fields still use the existing geometry, score, same-score tie rule and source ordering.

Each SX/S/X workspace contributes to total metrics. `file_*_bytes` counts bytes read/written by the application; it does not measure physical device traffic. Closing after reduction discards unneeded dirty cache entries rather than writing them again. Any computation/decode/encoding failure returns no product and closes its iterator/private scratch. These temporary files are not crash-recovery checkpoints.

## C: reuse only completed verified sources

Every attempt opens a fresh verified marker first. An in-process, bounded source receipt contains the complete ordered cut-key inventory, gate counts, metadata hashes and the earliest cut-cache expiration; it owns no extra arrays. Identity includes namespace, bucket/path, schema, physical and logical manifest entries, source times, network/execution/config identity and flag policy.

Only if every cut is still in the existing bounded readonly cut cache can staging/decoding/QC be skipped. A partial, expired or evicted inventory uses the original verified reader and parser. No partial receipt is installed if the generator stops before all cuts are consumed. Schema-3 first misses still verify all physical packs and the full logical digest. Reuse is disabled by the existing `cache_max_bytes=0`; this patch does not silently enable a larger cache. No claim of rereading every remote object on a valid hit: trusted, immutable in-process values are reused after the fresh marker check.

## D: immutable raw views

Within one VOR→NMR→RDR→CF review call, registered native arrays are snapshotted once into bytes-backed immutable buffers. Repeated VOR/RDR conversions share a task-local view; CF keeps a distinct factory policy because its ray-time and original-index handling differs. Fields with identical raw owners can share immutable storage, never writable masks or final decisions. Input-owner changes observed during the call fail; the scope is cleared on every exit. No cross-task native pointer cache. Canonical array hashing is bounded-block, same C traversal, dtype/endianness, NaN normalization and digest header; it is not sampled and not skipped.

## D: validation reads and source geometry

A serialized VOR sweep validation uses one 64 MiB LRU for immutable whole-field reads across all nested before-state views. Keys are physical field names of that exact group; aliases remain distinct where they point to different before-state fields. All validators and numerical comparisons execute as before. Partial slices are not promoted to whole-field evidence. Oversized fields bypass retention, not checks. This is additional bounded working memory per validation lane, not an entire-process memory limit.

RDR preparation caches only range-block IDs, fine IDs and geometry/time-compatible neighboring rows within one sweep. An excluded target-ray mask is expressed by readonly indexing rather than an entire copied availability matrix. Training, target and guard membership, donor order, reference hashes and fitted source statistics remain unchanged. There is no fitted-model cache, recursive donor propagation, or new Numba kernel.


## Deployment and verification

Run `make test-performance-cd` in the declared uv environment. The runner extracts the immutable pre-CD Git tree (`4c87cc83dc0036b303d904c108d292894dd39f28`) into a temporary directory; CI fetches that exact revision. An explicit `RAINPULSE_CD_REFERENCE_ROOT` remains supported. Do not overlay the delivery's reference/test snapshots onto the working tree.

105 uses `deploy/docker-compose.performance-cd-20260927.yaml` appended to its complete active Compose manifest. Automatic S keeps two replicas on `performance-cd-qc-20260927`; managed S and X use `performance-cd-full-20260927-r1`. These are overlays on the two corresponding A/B images, with every overwritten source file checked against its fixed baseline. The full image's final revision only removes an unused import, orders imports and wraps lines. Keep managed S's 40 GiB memory/swap limit and one numerical lane. Roll back by removing only this override under the same drained release procedure, preserving all prior overrides.

The automatic QC release gate must verify zero jobs/outbox/intents and no consumer backlog, then validate all replica image/config/flag/runtime identities before resuming. Drain managed QC/multiband independently and check fresh worker fingerprints when submitting new plans. Never replace the dirty server source checkout. Build staging and private replay requests are temporary; retain compact receipts under `runtime/control/performance-cd-*.json`.

Verification on 2026-09-27:

- C/D: 131 passed, 3 Numba skips on macOS and the Linux candidate image; actual Zarr 2 reads executed. A/B 146 passed/7 Numba skips; multiband 60, VOR 87, RDR 145, CF 110, worker/contracts/operations 55 passed. Relevant Go control-plane suites passed. New C/D source/tests/benchmark pass Ruff. Existing algorithm-tree lint count is 3349 versus 3350 at A/B; existing OpenAPI and RFI/paper identity failures remain under the user's accepted CI exception.
- Real S, identical frozen Z9598 request/config/contexts, one CPU: 149.379 s before, 149.168 s after. All 28,614 metadata/decompressed-chunk digests match. Peak process RSS was 25.696/25.566 GB. No material whole-worker speedup is established. Compressed bytes are not a canonical numerical comparison; transport validation remains unchanged.
- Real X, identical ZF505 request: 3.558 s before, 3.244 s after; all 55 output objects match byte-for-byte. Current execution has streaming false and decoded cache budget zero, so this is compatibility evidence, not proof of C's operational gain.
- Linux C synthetic mechanism, 16 cuts, 120×96 cells, 6 levels, SX/S/X: 64 MiB budget 1047.2→149.7 ms, with no new height scratch writes; 1 MiB budget 979.8→256.0 ms. All output digests match. Single samples, not p95/p99 or full-network throughput; application I/O counters are not physical device I/O.

No thresholds, missing/no-rain semantics, source attribution, product admission or streaming/cache defaults were changed. S/X numerical fusion remains subject to the existing network/geometry/calibration acceptance gates.

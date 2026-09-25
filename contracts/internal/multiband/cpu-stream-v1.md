# Multiband CPU stream execution v1

This is an opt-in execution contract, not a QC threshold/network/geometry change.
The existing operations pool, job payload, input catalog, reader and publisher
remain the owners. No new service, GPU backend or automatic station activation.

## Selection and identity

`RAINPULSE_MULTIBAND_EXECUTION_CONFIG` names a small local JSON file validated by
`ExecutionOptions`. Absent means the legacy eager route (with shared-array and
geometry optimizations). NumPy is default. Numba must be explicitly selected,
installed and warmed; no silent fallback. Policy is frozen at worker startup,
rechecked before every job, and written as an execution digest/backend receipt.
The current Go payload has no mandatory execution digest; this release therefore
requires coordinated code/image/settings deployment and draining frozen tasks.
A supplied `payload.execution_sha256` is checked; this does not claim the planner
already emits it. No hot configuration mutation. Resume/retry on a different
policy requires a new controlled execution; don't mix worker policies in one pool.

## Input and memory contract

A task consumes stations in `(radar_id, scan_id)` order and each station's cuts
in increasing number. At most 16 stations remains unchanged. The streamed route
permits <=64 cuts and configured cumulative gate limits, while eager `native-v1`
retains <=32 cuts / 8,000,000 gates. A streamed individual cut still has the old
8,000,000-gate and native geometry bounds, plus explicit decoded byte bounds.
A phase operation receives the entire range sequence of one cut; no phase reset
at arbitrary tiles. Existing S quality is translated, not recomputed.

Normalized Zarr is opened read-only over the frozen manifest mapping. Metadata
is preflighted before reading values; one cut's selected fields are decoded at
a time. Legacy packed schema3 MUST verify every physical pack and the full
logical aggregate, including unselected content, before exposing any entry.
Sequential private disk staging replaces full-pack RAM accumulation. One physical
object is still delivered as bytes by the existing transport, so over-budget
objects reject BEFORE download. This is not network byte-range streaming.

`native_bundle` is staged to a seekable file and decoded cut-by-cut with NPY
headers, sizes, names, dtypes and array checksum checked. No pickle, unknown
matrix dimensions, duplicate entries or NaN-as-no-echo conversion is allowed.
Whole-FMT decompression/decoder streaming is OUTSIDE this batch.

Array sharing means read-only views over caller-owned immutable input. It does
not protect against a caller that mutates its original array after the call.
Mutation targets (e.g. propagation eligibility) own a copy; untouched observations
are not duplicated. The existing logical `.nbytes` count is conservative and may
count aliases twice; it is not RSS. Cached cuts are immutable and byte/TTL/entry
bounded. Source manifests are checked every task, even on cut-cache hits.

## Fusion, determinism and storage

Shared eager/stream helpers implement unchanged candidate scoring, expiry,
beam support, `1e-12` ties, age/resolution tie breaks and first-in stable ordering.
Only same-station ground distance/bearing and output coordinates are reused.
Actual cut coordinates, beam geometry and per-ray age remain evaluated separately.
No final source decision or age mask is cached across product times.

Source-major streaming holds only current input/QC cut(s) and bounded cache.
Grid-height winner state is 36 bytes per height voxel, NOT stations x heights x
pixels. It uses memory only up to an explicit limit; larger state is private
memory-mapped tile storage with at most one tile open. Page cache, mapping faults,
state I/O, output arrays and codec buffers remain real resource costs. Container
RSS, disk bandwidth and capacity need operational measurement.

A late source error aborts the task: no partial result is returned/published.
Private workspace and native spool are cleaned on success or exception. Crash
cleanup requires OS/deployment scratch retention; this is not a distributed disk
transaction. Never point scratch at original input or another job's directory.

## Outputs

Normal 2D composite fields/PNG and source metadata retain their definitions.
The streaming product adds an execution receipt (not meteorological probability).
Standalone streamed `x_qc` writes the same two native object filenames with the
NEW explicit `rainpulse.multiband.native-stream-v1` metadata contract, up to64cuts
and4096 NPY entries. Old eager native-v1 readers intentionally reject it. A
controlled exporter may rename native_volume.json/native_arrays.npz to
volume.json/arrays.npz before using the streaming native_bundle reader; it must
not register this as legacy qc.zarr or pretend it passed S QC.

NPZ is written incrementally to bounded local scratch. Final publisher still
receives bounded encoded bytes; no claim of streaming upload or unlimited output.
No parent QC thresholds, operational eligibility, QPE settings, S/RDR/CF profile,
network size/cadence/CR definitions or image colors are changed by these settings.

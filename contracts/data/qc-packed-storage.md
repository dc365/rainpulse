# QC packed storage v1

Opt-in `RAINPULSE_QC_PACKED_STORAGE=1` applies only to bundles containing
`qc/summary.json`. Zarr logical keys, bytes, asset size and digest stay unchanged.
Publication marker schema 3.0 lists physical, uncompressed concatenation packs
in `objects`, and logical keys as `[key, pack, offset, length]` in `packed_entries`.
Packs target 8 MiB; individual larger entries remain intact. Offsets partition
each pack exactly. Readers verify physical hashes, bounds, unique safe logical
keys, complete coverage, and the original logical bundle digest before returning.
Schema 2.0 remains readable. Commit marker stays conditional and last.

Deploy compatible readers to every QC consumer before enabling packed writes.
Do not delete historical assets during rollout. Larger 128x1024 QC chunks are
independently configurable; array values and missing/quality states must match.

## Validation and rollout

Run `algorithms/.venv/bin/python -m pytest algorithms/tests/test_object_store.py algorithms/tests/test_benchmark_radar_qc.py`.
The synthetic 20,000 x 2,048-byte test produces five packs (plus one commit
marker), preserves every logical byte and deterministic ordering, and rejects
duplicate entries, incomplete coverage and incorrect offsets. Empty entries
and entries larger than a pack target are covered. This is not a measured
production latency result.

The production radial Compose overlay enables packed writes and 128x1024
chunks. Its QC and diagnostics image includes the new Python reader; grid
workers mount the same reader, preserving their existing image and scaling.
Go serves separately published diagnostic images, which remain unpacked.
Its generic single-object API does not resolve packed QC array keys; use the
Python artifact reader for those. Existing data is not rewritten or deleted.

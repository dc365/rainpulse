# Runtime telemetry: performance A+B v1

## Scope and ownership

`rainpulse.performance-ab-v1` is a bounded JSON log record, emitted as
`performance.trace` to the existing process stderr. It is not a QC product,
workflow result event, health acceptance decision, or new service.
`RAINPULSE_PERFORMANCE_TELEMETRY=0` disables the new observer; default is enabled.
Disabling observation does not disable the four numerical execution optimizations.

No timing, RSS, compile duration or sampling interval is added to a numerical
manifest, profile parameters hash, raw array, QC summary or existing result metric
schema. Existing runtime logs and scalar observability remain compatible.
Code fingerprints naturally change when the source changes; frozen tasks still
need coordinated release handling. No old frozen identity is overridden.

## Record

- `schema`, `event`: fixed identifiers above.
- `call_status`: `returned` or `raised` for the decorated root call. In particular,
  `Engine.run()` can return after handling a failed/cancelled task. This field
  MUST NOT be interpreted as job success. Use the existing workflow outcome.
- `identity`: only whitelisted job/run/attempt/trace identifiers and existing
  profile/network/execution/release hashes and execution mode. Configuration
  file contents, paths, object URIs, claims/tokens and credentials are excluded.
  Unknown identities are omitted, never guessed from a dirty checkout. Operations
  `frozen_*` values come from the claim; `startup_*` values come from the actual
  adapter/startup network. They remain separate even on identity mismatch.
- `runtime`: loaded Python and library versions, including explicit not-installed.
- `thread_limits`: only numeric values of the four OMP/OpenBLAS/MKL/Numba limits;
  `cpu_affinity_count` is a process affinity count, not physical cores or a quota.
- `stages[path]`: call count, raised count, accumulated inclusive duration,
  accumulated self duration, maximum call duration, `self_time_complete`.
  Durations are milliseconds. A path is a hierarchy of named spans.
- `counters`: bounded scalar work counts and selected existing metrics prefixed
  `reported.`. Their source semantics remain unchanged: cache totals are not
  automatically task deltas and resident array bytes are not RSS.
- `resources`: start/end gauges, nonnegative cumulative-counter deltas, a sampled
  process RSS peak LOWER BOUND (250 ms sampling), sample count, and scope.
  Process lifetime/cgroup highwater gauges are not task peaks. Process/container
  CPU and I/O may include other concurrent work. Unsupported metrics are absent.
- `dropped_stages`: observer saturation; does not alter computation.

`request_age_at_start_seconds` is age since request creation, NOT queue wait.
A future timestamp is recorded separately. Accurate queue wait requires existing
broker/operations admission and claim timestamps and is not invented here.

## Timing rules

Inclusive duration includes child spans. Self duration subtracts the UNION of
completed direct-child intervals (overlaps are not summed). Parallel siblings
may overlap and must not be added as sequential elapsed time. 256 stage paths,
128 counters, and 4096 direct-child intervals per invocation are the observer
limits. Interval overflow yields `self_ms=null` with incomplete status, not a
false precise duration. The root keeps a reserved stage slot.

ThreadPool reader jobs receive an independent copied Context; asyncio.to_thread
preserves the active trace. Generators time resume/send/throw/close work, not the
consumer's suspension interval. There is at most one temporary resource sampler
per active root; it is stopped on success and error. Observer errors never
replace the original result or exception. The very small root construction and
log emission cost is outside its body duration and is still part of real Worker
wall time.

## Existing timing compatibility

S `total_compute_ms` retains its historical pre-volume-review boundary.
`base_compute_ms` documents that boundary. New `volume_extensions_ms` and
`complete_compute_ms` include VOR/NMR/RDR/CF at the runner boundary. Worker read,
context, serialization, validation and publication are separate nested spans.
Existing profile/result formats are not relabelled.

## Acceptance

For the same frozen input/config/context, compare RAW, masks, actions, causes,
QI, trust, QPE, CR winner provenance and PNG bytes. Compare corruption rejection
for grouped validators. No observer field may appear in product payloads.
Evaluate cold/first-JIT separately from warm/cached work and preserve original
runtime fingerprints. Histograms/P95/P99 require enough real tasks; synthetic
subkernel timings cannot establish station/network capacity.

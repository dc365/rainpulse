# Operations v1 — first usable management slice

Contract version: 1.0. This is an additive control-plane contract, not a replacement
for weather data contracts or the automatic pipeline. HTTP paths are under
`/api/v1/admin/ops` (administrator bearer authentication) and `/internal/ops/v1`
(separate worker bearer authentication). Bodies are UTF-8 JSON, timestamps UTC,
identifiers canonical UUIDs. Unknown JSON fields on commands are rejected.

Implemented presets: `qc_preview` (normalized polar input -> unchanged QC ->
standalone review images), `render_only` (existing QC -> review images),
`diagnostics` (clone the frozen request of an existing analysis-diagnostics job).
All outputs are immutable **candidates**, never a default-publication switch.
The automatic pipeline's jobs, asset pointers, QI and eligibility are untouched.
Legacy jobs of all types are searchable/readable; arbitrary legacy jobs are NOT
implicitly made safe to retry by relabelling their status.

Plan flow: POST /plans -> checks, specs, input-marker digests, target fingerprints,
impact, expiration (15 min). POST /plans/{id}/submit with `idempotency_key` ->
durable run. Repeated submission of one plan returns its one run; a key reused
for a different plan is a conflict. Worker availability, inputs and effective
configuration are rechecked, never silently substituted at submission.

Tasks are WAITING, QUEUED, RUNNING, COMMITTING, SUCCEEDED, FAILED, BLOCKED or
CANCELLED. Display STALLED is a derived heartbeat warning, not an automatic
reclaim. A claim is the real execution start; publishing a NATS reference is not.
Run status additionally includes PARTIAL_SUCCESS, PAUSED and CANCELLING.

Each claim has an attempt UUID, ordinal and secret token (only its SHA256 is
stored). Outbox refs contain task ID and generation, no data arrays. Existing
PostgreSQL outbox + NATS deliver references at least once. Claim CAS serializes
execution rights. Attempt-specific output prefixes prevent a stale worker from
winning a newer attempt's artifact marker. A lost lease never automatically
starts duplicate expensive computation. Heartbeat loss is shown explicitly.

Finish verifies the committed marker against the attempt's exact URI and
job/run/trace identities. Parent success unlocks only its child. A cancellation
never unlocks children. A stale token cannot finish or heartbeat a newer attempt.
Missing result registration can be recovered from the same immutable marker.
Retry-failed reuses successful tasks and rechecks frozen identities; it is not
an alias for whole-chain recomputation or an automatic configuration upgrade.

GET /runs, /runs/{id}, /tasks/{id}, /tasks/{id}/events, /legacy,
/legacy/{id}, /inventory, /workers and /status are bounded queries. Run actions:
POST /runs/{id}/action {action: pause|resume|cancel|retry_failed|wake}.
`wake` explicitly republishes unclaimed references after a broker-retention loss;
claim generation prevents a duplicate execution.
POST /tasks/{id}/recover recovers registration without recomputing.
POST /tasks/{id}/abandon requires explicit `worker_stopped: true`, an expired
lease AND no fresh worker registration. It fences the old attempt, does not kill
an OS process, and requires a separate retry action.

Worker endpoints: POST /register, /claim, /heartbeat, /finish, /reconcile. No arbitrary shell,
Docker socket, filesystem path or SQL is accepted from the browser. Worker
identity is validated before claims and algorithms, plus before publication.
Mutation admission uses the existing release guard; telemetry/finish remain live
while admission is paused. Managed workers have separate exact NATS subjects,
never compete with realtime/old-background consumers.

Task logs are bounded structured lifecycle/algorithm-output/error records (up to
2,000 per attempt, 4,096 characters per line); overflow is reported, not hidden.
Heartbeat is not scientific progress. Full-system log search is an optional,
read-only Loki integration; absence is `not_configured`, not an empty success.
Current process RSS and lifetime ru_maxrss are distinct; neither is advertised as
exclusive memory attributable to a concurrent algorithm.

Public task results exclude claim secrets. GET /tasks/{id}/asset?key=... only
serves manifest-listed PNG/JSON from that task's committed candidate, with checksum
verification and an 8 MiB cap. No arbitrary object URI is proxied.

Machine-readable endpoint contract: `operations-openapi.json`. Plan hashing uses
canonical nested JSON to survive PostgreSQL jsonb key-order normalization.

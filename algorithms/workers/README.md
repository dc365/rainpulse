# Compute workers

RP-004 provides the reusable long-lived Worker runtime in
`rainpulse_algo.worker` and a `model.pysteps_lk` simulation handler. The runtime
uses a durable JetStream pull consumer, strict Pydantic event models,
deterministic result IDs, object-store marker idempotency, temporary output,
completion/failure publishing, and a health endpoint.

The simulation handler proves the execution and storage path only. RP-005 and
RP-006 replace its synthetic JSON payload with standardized real inputs and a
real pySTEPS-LK forecast while retaining this runtime boundary.

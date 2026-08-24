# Event contracts

The JSON Schemas in this directory are the source of truth for Go ↔ Python
events carried by NATS JetStream. Events use schema version `1.0`, carry only
task metadata and object-store URIs, and never contain meteorological arrays.

Delivery is at least once. Consumers must enforce `job_id` idempotency and ACK
only after validated output is atomically published. Examples in
`contracts/examples` are validated in the contract test suite.

## JetStream routing

- Stream: `RAINPULSE_JOBS`
- Task subject: `rainpulse.jobs.requested.<job_type_token>`
- RP-003 simulation task: `rainpulse.jobs.requested.model_pysteps_lk`
- Completion subject: `rainpulse.jobs.completed`
- Failure subject: `rainpulse.jobs.failed`
- Go terminal-result consumer: `rainpulse-orchestrator-results-v2`
- RP-004 simulation Worker consumer: `rainpulse-sim-worker`

Publishers set the event UUID as the JetStream message ID. The Go consumer
records every processed terminal result in `inbox_events` inside the same
database transaction as the job/run update, so redelivery and conflicting
success/failure results are safe.

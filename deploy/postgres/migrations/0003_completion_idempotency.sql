CREATE UNIQUE INDEX inbox_events_job_event_type_uidx
    ON inbox_events (job_id, event_type);

COMMENT ON INDEX inbox_events_job_event_type_uidx IS
    'Enforces job-level idempotency even if a worker retries with a new event UUID.';

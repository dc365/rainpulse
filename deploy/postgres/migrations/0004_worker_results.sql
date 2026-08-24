DROP INDEX inbox_events_job_event_type_uidx;

CREATE UNIQUE INDEX inbox_events_job_terminal_result_uidx
    ON inbox_events (job_id)
    WHERE event_type IN ('job.completed', 'job.failed');

COMMENT ON INDEX inbox_events_job_terminal_result_uidx IS
    'A job may apply exactly one terminal Worker result, even across redelivery or conflicting event types.';

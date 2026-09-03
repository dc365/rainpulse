CREATE TABLE workspace_projections (
    projection_key TEXT PRIMARY KEY,
    status_code SMALLINT NOT NULL,
    headers JSONB NOT NULL DEFAULT '{}'::JSONB,
    body BYTEA NOT NULL,
    etag TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    stale_until TIMESTAMPTZ NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status_code BETWEEN 200 AND 299),
    CHECK (stale_until >= expires_at),
    CHECK (octet_length(body) <= 16777216)
);

CREATE INDEX workspace_projections_expiry_idx
    ON workspace_projections (stale_until, last_accessed_at);

ALTER TABLE pipeline_regeneration_requests
    ADD COLUMN cancel_reason TEXT,
    ADD COLUMN cancelled_at TIMESTAMPTZ;

ALTER TABLE pipeline_regeneration_requests
    ADD CONSTRAINT pipeline_regeneration_requests_cancellation_check CHECK (
        (cancel_reason IS NULL AND cancelled_at IS NULL)
        OR (cancel_reason IS NOT NULL AND cancelled_at IS NOT NULL)
    );

ALTER TABLE outbox_events
    DROP CONSTRAINT IF EXISTS outbox_events_status_check;
ALTER TABLE outbox_events
    ADD CONSTRAINT outbox_events_status_check
        CHECK (status IN ('pending', 'publishing', 'published', 'failed', 'cancelled'));

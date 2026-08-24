ALTER TABLE forecast_runs DROP CONSTRAINT forecast_runs_status_check;
UPDATE forecast_runs
SET status = CASE status
    WHEN 'queued' THEN 'WAITING'
    WHEN 'running' THEN 'BASELINE_RUNNING'
    WHEN 'completed' THEN 'PUBLISHED'
    WHEN 'degraded' THEN 'DEGRADED'
    WHEN 'failed' THEN 'FAILED'
    WHEN 'cancelled' THEN 'SKIPPED'
    ELSE status
END;
ALTER TABLE forecast_runs ALTER COLUMN status SET DEFAULT 'WAITING';
ALTER TABLE forecast_runs ADD CONSTRAINT forecast_runs_status_check CHECK (
    status IN (
        'WAITING', 'RECEIVED', 'VALIDATING', 'PREPROCESSING',
        'BASELINE_RUNNING', 'BASELINE_READY', 'ENHANCED_RUNNING',
        'PRODUCT_BUILDING', 'PUBLISHED', 'VERIFYING', 'VERIFIED',
        'DEGRADED', 'FAILED', 'SKIPPED'
    )
);

ALTER TABLE jobs DROP CONSTRAINT jobs_status_check;
UPDATE jobs
SET status = CASE status
    WHEN 'pending' THEN 'PENDING'
    WHEN 'published' THEN 'PENDING'
    WHEN 'running' THEN 'RUNNING'
    WHEN 'retrying' THEN 'PENDING'
    WHEN 'completed' THEN 'SUCCEEDED'
    WHEN 'failed' THEN 'FAILED'
    WHEN 'cancelled' THEN 'SKIPPED'
    ELSE status
END;
ALTER TABLE jobs ALTER COLUMN status SET DEFAULT 'PENDING';
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED'));
ALTER TABLE jobs ADD COLUMN trace_id UUID;
UPDATE jobs SET trace_id = job_id WHERE trace_id IS NULL;
ALTER TABLE jobs ALTER COLUMN trace_id SET NOT NULL;
ALTER TABLE jobs ADD COLUMN request_payload JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE job_attempts DROP CONSTRAINT job_attempts_status_check;
UPDATE job_attempts
SET status = CASE status
    WHEN 'running' THEN 'RUNNING'
    WHEN 'completed' THEN 'SUCCEEDED'
    WHEN 'failed' THEN 'FAILED'
    WHEN 'timed_out' THEN 'FAILED'
    ELSE status
END;
ALTER TABLE job_attempts ADD CONSTRAINT job_attempts_status_check
    CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED'));

CREATE TABLE inbox_events (
    event_id UUID PRIMARY KEY,
    event_type TEXT NOT NULL,
    run_id UUID NOT NULL REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
    payload JSONB NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX inbox_events_job_idx ON inbox_events (job_id, processed_at DESC);

INSERT INTO config_versions (config_version, sha256, config, description)
VALUES (
    'rp003-sim-v1',
    '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a',
    '{}'::JSONB,
    'RP-003 control-plane simulation only'
)
ON CONFLICT (config_version) DO NOTHING;

INSERT INTO model_versions (
    model_id, model_version, model_type, config_version, enabled, metadata
)
VALUES (
    'pysteps-lk-sim',
    'pysteps-lk-sim-v1',
    'simulation',
    'rp003-sim-v1',
    FALSE,
    '{"simulation": true}'::JSONB
)
ON CONFLICT (model_id, model_version) DO NOTHING;

ALTER TABLE analysis_cycles
    ADD COLUMN mosaic_uri TEXT;

CREATE TABLE mosaic_runs (
    analysis_id UUID PRIMARY KEY REFERENCES analysis_cycles(analysis_id) ON DELETE RESTRICT,
    job_id UUID NOT NULL UNIQUE REFERENCES jobs(job_id) ON DELETE RESTRICT,
    mosaic_config_version TEXT NOT NULL,
    mosaic_algorithm_version TEXT NOT NULL,
    status TEXT NOT NULL,
    mosaic_uri TEXT,
    diagnostics JSONB,
    measured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK ((status = 'SUCCEEDED' AND mosaic_uri IS NOT NULL AND diagnostics IS NOT NULL)
        OR status <> 'SUCCEEDED')
);

CREATE INDEX mosaic_runs_status_idx ON mosaic_runs (status, updated_at DESC);

CREATE TABLE diagnostic_runs (
    job_id UUID PRIMARY KEY REFERENCES jobs(job_id) ON DELETE RESTRICT,
    analysis_id UUID NOT NULL REFERENCES analysis_cycles(analysis_id) ON DELETE RESTRICT,
    diagnostic_config_version TEXT NOT NULL,
    renderer_version TEXT NOT NULL,
    input_analysis_uri TEXT NOT NULL,
    status TEXT NOT NULL,
    bundle_uri TEXT,
    manifest JSONB,
    measured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (analysis_id, diagnostic_config_version, renderer_version),
    CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK ((status = 'SUCCEEDED' AND bundle_uri IS NOT NULL AND manifest IS NOT NULL)
        OR status <> 'SUCCEEDED')
);

CREATE INDEX diagnostic_runs_analysis_idx
    ON diagnostic_runs (analysis_id, updated_at DESC);
CREATE INDEX diagnostic_runs_status_idx
    ON diagnostic_runs (status, updated_at DESC);

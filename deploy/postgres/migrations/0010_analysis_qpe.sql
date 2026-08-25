CREATE TABLE qpe_runs (
    job_id UUID PRIMARY KEY REFERENCES jobs(job_id) ON DELETE RESTRICT,
    analysis_id UUID NOT NULL REFERENCES analysis_cycles(analysis_id) ON DELETE RESTRICT,
    qpe_config_version TEXT NOT NULL,
    qpe_algorithm_version TEXT NOT NULL,
    input_mosaic_uri TEXT NOT NULL,
    status TEXT NOT NULL,
    analysis_uri TEXT,
    diagnostics JSONB,
    measured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (analysis_id, qpe_config_version, qpe_algorithm_version),
    CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK ((status = 'SUCCEEDED' AND analysis_uri IS NOT NULL AND diagnostics IS NOT NULL)
        OR status <> 'SUCCEEDED')
);

CREATE INDEX qpe_runs_analysis_idx ON qpe_runs (analysis_id, updated_at DESC);
CREATE INDEX qpe_runs_status_idx ON qpe_runs (status, updated_at DESC);

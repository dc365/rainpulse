ALTER TABLE forecast_runs DROP CONSTRAINT forecast_runs_status_check;
ALTER TABLE forecast_runs ADD CONSTRAINT forecast_runs_status_check CHECK (
    status IN (
        'WAITING', 'RECEIVED', 'VALIDATING', 'PREPROCESSING', 'INPUT_READY',
        'BASELINE_RUNNING', 'BASELINE_READY', 'ENHANCED_RUNNING',
        'PRODUCT_BUILDING', 'PUBLISHED', 'VERIFYING', 'VERIFIED',
        'DEGRADED', 'FAILED', 'SKIPPED'
    )
);

CREATE TABLE nowcast_input_runs (
    job_id UUID PRIMARY KEY REFERENCES jobs(job_id) ON DELETE RESTRICT,
    run_id UUID NOT NULL UNIQUE REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    issue_time TIMESTAMPTZ NOT NULL,
    grid_id TEXT NOT NULL,
    preprocess_version TEXT NOT NULL,
    gate_config_version TEXT NOT NULL,
    status TEXT NOT NULL,
    input_uri TEXT,
    diagnostics JSONB,
    measured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (issue_time, grid_id, preprocess_version, gate_config_version),
    CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK ((status = 'SUCCEEDED' AND input_uri IS NOT NULL AND diagnostics IS NOT NULL)
        OR status <> 'SUCCEEDED')
);

CREATE TABLE nowcast_input_frames (
    job_id UUID NOT NULL REFERENCES nowcast_input_runs(job_id) ON DELETE RESTRICT,
    frame_index SMALLINT NOT NULL,
    analysis_id UUID NOT NULL REFERENCES analysis_cycles(analysis_id) ON DELETE RESTRICT,
    analysis_time TIMESTAMPTZ NOT NULL,
    input_uri TEXT NOT NULL,
    PRIMARY KEY (job_id, frame_index),
    UNIQUE (job_id, analysis_id),
    CHECK (frame_index BETWEEN 0 AND 5)
);

CREATE INDEX nowcast_input_runs_issue_idx
    ON nowcast_input_runs (issue_time DESC, grid_id);
CREATE INDEX nowcast_input_runs_status_idx
    ON nowcast_input_runs (status, updated_at DESC);

CREATE TABLE pipeline_regeneration_requests (
    request_id UUID PRIMARY KEY,
    source_run_id UUID NOT NULL REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    target_run_id UUID NOT NULL UNIQUE REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    issue_time TIMESTAMPTZ NOT NULL,
    grid_id TEXT NOT NULL,
    preset TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (preset = 'forecast_all'),
    CHECK (status IN (
        'PENDING', 'QC_RUNNING', 'GRID_RUNNING', 'MOSAIC_RUNNING',
        'QPE_RUNNING', 'NOWCAST_RUNNING', 'SUCCEEDED', 'FAILED'
    ))
);

CREATE TABLE pipeline_regeneration_frames (
    request_id UUID NOT NULL REFERENCES pipeline_regeneration_requests(request_id) ON DELETE RESTRICT,
    frame_index SMALLINT NOT NULL CHECK (frame_index BETWEEN 0 AND 5),
    source_analysis_id UUID NOT NULL REFERENCES analysis_cycles(analysis_id) ON DELETE RESTRICT,
    analysis_time TIMESTAMPTZ NOT NULL,
    regenerated_analysis_id UUID REFERENCES analysis_cycles(analysis_id) ON DELETE RESTRICT,
    PRIMARY KEY (request_id, frame_index),
    UNIQUE (request_id, source_analysis_id)
);

CREATE TABLE pipeline_regeneration_frame_scans (
    request_id UUID NOT NULL,
    frame_index SMALLINT NOT NULL,
    radar_id TEXT NOT NULL REFERENCES radars(radar_id) ON DELETE RESTRICT,
    scan_id UUID NOT NULL REFERENCES radar_scans(scan_id) ON DELETE RESTRICT,
    PRIMARY KEY (request_id, frame_index, radar_id),
    FOREIGN KEY (request_id, frame_index)
        REFERENCES pipeline_regeneration_frames(request_id, frame_index) ON DELETE RESTRICT
);

ALTER TABLE jobs
    ADD COLUMN regeneration_request_id UUID
        REFERENCES pipeline_regeneration_requests(request_id) ON DELETE RESTRICT;

ALTER TABLE jobs
    DROP CONSTRAINT IF EXISTS jobs_run_id_job_type_model_id_model_version_config_version_key;

ALTER TABLE analysis_cycles
    DROP CONSTRAINT IF EXISTS analysis_cycles_analysis_time_grid_id_config_version_key;

CREATE INDEX jobs_regeneration_request_idx
    ON jobs (regeneration_request_id, job_type, status, created_at);

CREATE INDEX analysis_cycles_lineage_idx
    ON analysis_cycles (analysis_time DESC, grid_id, config_version, created_at DESC);

CREATE INDEX pipeline_regeneration_active_idx
    ON pipeline_regeneration_requests (created_at)
    WHERE status NOT IN ('SUCCEEDED', 'FAILED');

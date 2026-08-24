CREATE TABLE workflow_runs (
    run_id UUID PRIMARY KEY,
    run_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (run_type IN ('radar_scan', 'analysis_cycle', 'forecast_run'))
);

INSERT INTO workflow_runs (run_id, run_type, created_at)
SELECT run_id, 'forecast_run', created_at
FROM forecast_runs
ON CONFLICT (run_id) DO NOTHING;

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_run_id_fkey;
ALTER TABLE jobs ADD CONSTRAINT jobs_workflow_run_id_fkey
    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT;

ALTER TABLE inbox_events DROP CONSTRAINT IF EXISTS inbox_events_run_id_fkey;
ALTER TABLE inbox_events ADD CONSTRAINT inbox_events_workflow_run_id_fkey
    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT;

ALTER TABLE forecast_runs ADD CONSTRAINT forecast_runs_workflow_run_id_fkey
    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT;

CREATE TABLE radars (
    radar_id TEXT PRIMARY KEY,
    display_name TEXT,
    lifecycle TEXT NOT NULL DEFAULT 'draft',
    current_config_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (lifecycle IN ('draft', 'ready', 'disabled'))
);

CREATE TABLE radar_config_versions (
    radar_id TEXT NOT NULL REFERENCES radars(radar_id) ON DELETE RESTRICT,
    radar_config_version TEXT NOT NULL,
    config JSONB NOT NULL,
    sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (radar_id, radar_config_version),
    UNIQUE (radar_id, sha256),
    CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

ALTER TABLE radars ADD CONSTRAINT radars_current_config_fkey
    FOREIGN KEY (radar_id, current_config_version)
    REFERENCES radar_config_versions(radar_id, radar_config_version)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE radar_scans (
    scan_id UUID PRIMARY KEY,
    radar_id TEXT NOT NULL REFERENCES radars(radar_id) ON DELETE RESTRICT,
    raw_asset_id UUID,
    volume_start_time TIMESTAMPTZ NOT NULL,
    volume_end_time TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (radar_id, volume_start_time, volume_end_time),
    CHECK (volume_end_time >= volume_start_time),
    CHECK (received_at >= volume_start_time)
);

CREATE TABLE radar_scan_runs (
    run_id UUID PRIMARY KEY REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    scan_id UUID NOT NULL UNIQUE REFERENCES radar_scans(scan_id) ON DELETE RESTRICT,
    radar_id TEXT NOT NULL,
    radar_config_version TEXT NOT NULL,
    status TEXT NOT NULL,
    degraded_reason TEXT,
    normalized_uri TEXT,
    qc_uri TEXT,
    grid_uri TEXT,
    scan_completeness DOUBLE PRECISION,
    mean_quality_index DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (radar_id, radar_config_version)
        REFERENCES radar_config_versions(radar_id, radar_config_version) ON DELETE RESTRICT,
    CHECK (status IN (
        'RAW_RECEIVED', 'RAW_VALIDATING', 'DECODING', 'NORMALIZED',
        'QC_RUNNING', 'QC_READY', 'GRID_RUNNING', 'RADAR_GRID_READY',
        'DEGRADED', 'FAILED', 'SKIPPED'
    )),
    CHECK (scan_completeness IS NULL OR scan_completeness BETWEEN 0 AND 1),
    CHECK (mean_quality_index IS NULL OR mean_quality_index BETWEEN 0 AND 1)
);

CREATE TABLE analysis_cycles (
    analysis_id UUID PRIMARY KEY,
    run_id UUID NOT NULL UNIQUE REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    analysis_time TIMESTAMPTZ NOT NULL,
    grid_id TEXT NOT NULL,
    config_version TEXT NOT NULL,
    status TEXT NOT NULL,
    degraded_reason TEXT,
    radar_count INTEGER NOT NULL DEFAULT 0 CHECK (radar_count >= 0),
    valid_coverage_ratio DOUBLE PRECISION,
    mean_quality_index DOUBLE PRECISION,
    analysis_uri TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (analysis_time, grid_id, config_version),
    CHECK (status IN (
        'OPEN', 'COLLECTING_RADARS', 'ALIGNING', 'MOSAIC_RUNNING',
        'QPE_RUNNING', 'ANALYSIS_READY', 'DEGRADED', 'FAILED', 'SKIPPED'
    )),
    CHECK (valid_coverage_ratio IS NULL OR valid_coverage_ratio BETWEEN 0 AND 1),
    CHECK (mean_quality_index IS NULL OR mean_quality_index BETWEEN 0 AND 1)
);

CREATE TABLE analysis_cycle_radars (
    analysis_id UUID NOT NULL REFERENCES analysis_cycles(analysis_id) ON DELETE RESTRICT,
    radar_id TEXT NOT NULL REFERENCES radars(radar_id) ON DELETE RESTRICT,
    scan_id UUID REFERENCES radar_scans(scan_id) ON DELETE RESTRICT,
    state TEXT NOT NULL,
    time_offset_seconds INTEGER,
    mean_quality_index DOUBLE PRECISION,
    exclusion_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (analysis_id, radar_id),
    CHECK (state IN ('PARTICIPATING', 'MISSING', 'FAILED', 'EXCLUDED')),
    CHECK (mean_quality_index IS NULL OR mean_quality_index BETWEEN 0 AND 1),
    CHECK ((state = 'PARTICIPATING' AND scan_id IS NOT NULL) OR state <> 'PARTICIPATING')
);

CREATE INDEX workflow_runs_type_created_idx
    ON workflow_runs (run_type, created_at DESC);
CREATE INDEX radar_scans_radar_time_idx
    ON radar_scans (radar_id, volume_start_time DESC);
CREATE INDEX radar_scan_runs_status_idx
    ON radar_scan_runs (status, updated_at DESC);
CREATE INDEX analysis_cycles_time_idx
    ON analysis_cycles (analysis_time DESC, grid_id);
CREATE INDEX analysis_cycles_status_idx
    ON analysis_cycles (status, updated_at DESC);

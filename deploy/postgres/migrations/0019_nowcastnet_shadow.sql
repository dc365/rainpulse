-- Formal lifecycle for the public-weight NowcastNet shadow.  It intentionally
-- does not drive forecast_runs: the baseline remains independently publishable.
CREATE TABLE algorithm_runs (
    algorithm_run_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    job_id UUID NOT NULL UNIQUE REFERENCES jobs(job_id) ON DELETE RESTRICT,
    algorithm_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    config_version TEXT NOT NULL REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    source_model_config_version TEXT NOT NULL,
    tile_atlas_version TEXT NOT NULL,
    input_analysis_ids UUID[] NOT NULL,
    input_analysis_uris TEXT[] NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    output_uri TEXT,
    output_sha256 CHAR(64),
    runtime_ms BIGINT CHECK (runtime_ms IS NULL OR runtime_ms >= 0),
    diagnostics JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    CHECK (algorithm_id = 'nowcastnet'),
    CHECK (cardinality(input_analysis_ids) = 9),
    CHECK (cardinality(input_analysis_uris) = 9),
    CHECK (status IN ('running', 'completed', 'failed', 'degraded')),
    CHECK (output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (completed_at IS NULL OR completed_at >= started_at),
    CHECK (
        status <> 'completed' OR
        (output_uri IS NOT NULL AND output_sha256 IS NOT NULL AND diagnostics IS NOT NULL)
    )
);

CREATE INDEX algorithm_runs_run_status_idx
    ON algorithm_runs (run_id, status, started_at DESC);
CREATE INDEX algorithm_runs_algorithm_status_idx
    ON algorithm_runs (algorithm_id, model_version, status, started_at DESC);

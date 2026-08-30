ALTER TABLE qpe_runs
    ADD COLUMN analysis_sha256 CHAR(64),
    ADD COLUMN analysis_size_bytes BIGINT;

UPDATE qpe_runs AS qpe
SET analysis_sha256 = committed.sha256,
    analysis_size_bytes = committed.size_bytes
FROM (
    SELECT DISTINCT ON (existing.job_id)
        existing.job_id,
        asset.value->>'sha256' AS sha256,
        (asset.value->>'size_bytes')::BIGINT AS size_bytes
    FROM qpe_runs AS existing
    JOIN job_attempts AS attempt
      ON attempt.job_id = existing.job_id AND attempt.status = 'SUCCEEDED'
    CROSS JOIN LATERAL jsonb_array_elements(attempt.metadata->'assets') AS asset(value)
    WHERE asset.value->>'asset_type' = 'radar_analysis'
      AND asset.value->>'sha256' ~ '^[0-9a-f]{64}$'
      AND asset.value->>'size_bytes' ~ '^[1-9][0-9]*$'
    ORDER BY existing.job_id, attempt.attempt_no DESC
) AS committed
WHERE qpe.job_id = committed.job_id;

ALTER TABLE qpe_runs ADD CONSTRAINT qpe_runs_analysis_sha256_check
    CHECK (analysis_sha256 IS NULL OR analysis_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE qpe_runs ADD CONSTRAINT qpe_runs_analysis_size_bytes_check
    CHECK (analysis_size_bytes IS NULL OR analysis_size_bytes > 0);

CREATE TABLE forecast_verification_runs (
    job_id UUID PRIMARY KEY REFERENCES jobs(job_id) ON DELETE RESTRICT,
    run_id UUID NOT NULL UNIQUE REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    forecast_uri TEXT NOT NULL,
    forecast_sha256 CHAR(64) NOT NULL,
    forecast_contract_version TEXT NOT NULL,
    profile_version TEXT NOT NULL REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    result_contract_version TEXT NOT NULL,
    truth_analysis_ids UUID[] NOT NULL,
    truth_valid_times TIMESTAMPTZ[] NOT NULL,
    truth_uris TEXT[] NOT NULL,
    truth_sha256s TEXT[] NOT NULL,
    status TEXT NOT NULL,
    result_uri TEXT,
    result_sha256 CHAR(64),
    result_size_bytes BIGINT,
    summary JSONB,
    measured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (forecast_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (forecast_contract_version = '1.1'),
    CHECK (result_contract_version = '1.0'),
    CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK (cardinality(truth_analysis_ids) = 24),
    CHECK (cardinality(truth_valid_times) = 24),
    CHECK (cardinality(truth_uris) = 24),
    CHECK (cardinality(truth_sha256s) = 24),
    CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (result_size_bytes IS NULL OR result_size_bytes > 0),
    CHECK (
        status <> 'SUCCEEDED'
        OR (
            result_uri IS NOT NULL
            AND result_sha256 IS NOT NULL
            AND result_size_bytes IS NOT NULL
            AND summary IS NOT NULL
            AND measured_at IS NOT NULL
        )
    )
);

CREATE INDEX forecast_verification_runs_status_idx
    ON forecast_verification_runs (status, updated_at DESC);
CREATE INDEX forecast_verification_runs_measured_at_idx
    ON forecast_verification_runs (measured_at DESC)
    WHERE status = 'SUCCEEDED';

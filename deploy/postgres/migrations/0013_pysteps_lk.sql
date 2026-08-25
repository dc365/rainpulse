ALTER TABLE model_runs
    ADD COLUMN input_uri TEXT,
    ADD COLUMN output_uri TEXT,
    ADD COLUMN diagnostics JSONB,
    ADD COLUMN measured_at TIMESTAMPTZ;

ALTER TABLE model_runs ADD CONSTRAINT model_runs_rp014_completion_check CHECK (
    status <> 'completed'
    OR (output_uri IS NOT NULL AND diagnostics IS NOT NULL AND measured_at IS NOT NULL)
);

CREATE INDEX model_runs_model_status_idx
    ON model_runs (model_id, model_version, status, started_at DESC);

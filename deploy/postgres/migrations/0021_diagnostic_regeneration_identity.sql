-- QC-only recomputation retains the analysis identity while replacing QC inputs.
-- Job identity (including regeneration ID) is the idempotency boundary; renderer
-- configuration alone cannot uniquely identify the rendered input bytes.
ALTER TABLE diagnostic_runs
    DROP CONSTRAINT diagnostic_runs_analysis_id_diagnostic_config_version_rende_key;

ALTER TABLE nowcast_input_runs
    DROP CONSTRAINT IF EXISTS nowcast_input_runs_issue_time_grid_id_preprocess_version_ga_key;

CREATE INDEX nowcast_input_runs_lineage_idx
    ON nowcast_input_runs (
        issue_time DESC,
        grid_id,
        preprocess_version,
        gate_config_version
    );

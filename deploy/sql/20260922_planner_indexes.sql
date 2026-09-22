-- Run explicitly with psql -v ON_ERROR_STOP=1, WITHOUT --single-transaction.
-- Additive, concurrent indexes; no radar data, task or artifact is removed.
CREATE INDEX CONCURRENTLY IF NOT EXISTS rp_planner_radar_time
    ON radar_scans (lower(radar_id), volume_end_time, scan_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS rp_planner_scan_stage
    ON radar_scan_runs (status, scan_id, run_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS rp_planner_qc_only
    ON qc_batch_items (item_id) WHERE kind = 'qc';
CREATE INDEX CONCURRENTLY IF NOT EXISTS rp_planner_analysis_stage
    ON analysis_cycles (grid_id, status, analysis_time, analysis_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS rp_planner_mosaic_owner
    ON mosaic_runs (analysis_id, job_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS rp_planner_forecast_stage
    ON forecast_runs (grid_id, status, issue_time, run_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS rp_planner_forecast_rerun
    ON forecast_runs (grid_id, status, issue_time, run_id) WHERE rerun_of IS NOT NULL;
-- IF NOT EXISTS cannot repair an invalid index left by an interrupted build.
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema() AND c.relname LIKE 'rp_planner_%'
          AND NOT i.indisvalid
    ) THEN RAISE EXCEPTION 'Invalid rp_planner index: inspect and repair before acceptance'; END IF;
END $$;

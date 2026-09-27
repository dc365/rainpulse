-- Unknown draft geometry must never be admitted as complete or healthy.
ALTER TABLE radar_health_metrics DROP CONSTRAINT radar_health_metrics_check1;
ALTER TABLE radar_health_metrics ADD CONSTRAINT radar_health_metrics_expected_radials_check
CHECK (actual_radial_count >= 0 AND (
    expected_radial_count > 0 OR (
        expected_radial_count = 0 AND health_state = 'UNAVAILABLE'
        AND scan_completeness = 0
        AND COALESCE(diagnostics->'health_reasons' ? 'SCAN_GEOMETRY_UNKNOWN', false)
    )
));

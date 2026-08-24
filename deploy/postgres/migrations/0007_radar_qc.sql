CREATE TABLE radar_qc_metrics (
    scan_id UUID PRIMARY KEY REFERENCES radar_scans(scan_id) ON DELETE RESTRICT,
    radar_id TEXT NOT NULL REFERENCES radars(radar_id) ON DELETE RESTRICT,
    qc_profile TEXT NOT NULL,
    qc_pipeline_version TEXT NOT NULL,
    flag_definition_version TEXT NOT NULL,
    health_state TEXT NOT NULL,
    mean_quality_index DOUBLE PRECISION NOT NULL,
    valid_gate_count BIGINT NOT NULL,
    missing_gate_count BIGINT NOT NULL,
    low_quality_gate_count BIGINT NOT NULL,
    no_rain_gate_count BIGINT NOT NULL,
    radial_interference_ray_count BIGINT NOT NULL,
    ground_clutter_gate_count BIGINT NOT NULL,
    sea_clutter_gate_count BIGINT NOT NULL,
    ap_gate_count BIGINT NOT NULL,
    module_statuses JSONB NOT NULL,
    diagnostics JSONB NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (health_state IN ('HEALTHY', 'DEGRADED')),
    CHECK (mean_quality_index BETWEEN 0 AND 1),
    CHECK (valid_gate_count >= 0),
    CHECK (missing_gate_count >= 0),
    CHECK (low_quality_gate_count >= 0),
    CHECK (no_rain_gate_count >= 0),
    CHECK (radial_interference_ray_count >= 0),
    CHECK (ground_clutter_gate_count >= 0),
    CHECK (sea_clutter_gate_count >= 0),
    CHECK (ap_gate_count >= 0)
);

CREATE INDEX radar_qc_metrics_radar_time_idx
    ON radar_qc_metrics (radar_id, measured_at DESC);

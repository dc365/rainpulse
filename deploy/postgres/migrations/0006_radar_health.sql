ALTER TABLE radar_scans
    ADD CONSTRAINT radar_scans_raw_asset_fkey
    FOREIGN KEY (raw_asset_id) REFERENCES input_assets(asset_id) ON DELETE RESTRICT;

CREATE TABLE radar_health_metrics (
    scan_id UUID PRIMARY KEY REFERENCES radar_scans(scan_id) ON DELETE RESTRICT,
    radar_id TEXT NOT NULL REFERENCES radars(radar_id) ON DELETE RESTRICT,
    health_profile_version TEXT NOT NULL,
    health_state TEXT NOT NULL,
    scan_completeness DOUBLE PRECISION NOT NULL,
    expected_sweep_count INTEGER NOT NULL,
    actual_sweep_count INTEGER NOT NULL,
    missing_sweep_numbers SMALLINT[] NOT NULL DEFAULT '{}',
    expected_radial_count INTEGER NOT NULL,
    actual_radial_count INTEGER NOT NULL,
    missing_radial_count INTEGER NOT NULL,
    maximum_azimuth_gap_deg DOUBLE PRECISION NOT NULL,
    field_availability JSONB NOT NULL,
    noise_level JSONB NOT NULL,
    channel_status TEXT NOT NULL,
    anomaly_count BIGINT NOT NULL,
    diagnostics JSONB NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (health_state IN ('HEALTHY', 'DEGRADED', 'UNAVAILABLE')),
    CHECK (scan_completeness BETWEEN 0 AND 1),
    CHECK (expected_sweep_count > 0 AND actual_sweep_count >= 0),
    CHECK (expected_radial_count > 0 AND actual_radial_count >= 0),
    CHECK (missing_radial_count >= 0),
    CHECK (maximum_azimuth_gap_deg BETWEEN 0 AND 360),
    CHECK (channel_status IN ('OK', 'DEGRADED', 'UNKNOWN')),
    CHECK (anomaly_count >= 0)
);

CREATE INDEX radar_health_metrics_radar_time_idx
    ON radar_health_metrics (radar_id, measured_at DESC);

CREATE TABLE product_build_runs (
    job_id UUID PRIMARY KEY REFERENCES jobs(job_id) ON DELETE RESTRICT,
    run_id UUID NOT NULL UNIQUE REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    model_run_id UUID NOT NULL REFERENCES model_runs(model_run_id) ON DELETE RESTRICT,
    forecast_uri TEXT NOT NULL,
    forecast_sha256 CHAR(64) NOT NULL,
    product_config_version TEXT NOT NULL REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    bundle_contract_version TEXT NOT NULL,
    status TEXT NOT NULL,
    bundle_uri TEXT,
    manifest JSONB,
    measured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (forecast_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (bundle_contract_version = '1.0'),
    CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK ((status = 'SUCCEEDED' AND bundle_uri IS NOT NULL AND manifest IS NOT NULL)
        OR status <> 'SUCCEEDED')
);

ALTER TABLE products
    ADD COLUMN valid_times TIMESTAMPTZ[],
    ADD COLUMN member_count SMALLINT NOT NULL DEFAULT 1,
    ADD COLUMN source_forecast_uri TEXT,
    ADD COLUMN source_forecast_sha256 CHAR(64);
ALTER TABLE products ADD CONSTRAINT products_rp015_distribution_check CHECK (
    status <> 'published'
    OR (
        valid_times IS NOT NULL
        AND cardinality(valid_times) > 0
        AND member_count > 0
        AND source_forecast_uri IS NOT NULL
        AND source_forecast_sha256 ~ '^[0-9a-f]{64}$'
    )
);

ALTER TABLE product_assets ADD COLUMN valid_time TIMESTAMPTZ;

CREATE UNIQUE INDEX products_rp015_identity_uidx
    ON products (run_id, model_run_id, product_type, config_version);
CREATE INDEX product_assets_product_lead_idx
    ON product_assets (product_id, lead_minutes, asset_type)
    WHERE deleted_at IS NULL;
CREATE INDEX product_assets_product_valid_time_idx
    ON product_assets (product_id, valid_time, asset_type)
    WHERE deleted_at IS NULL;
CREATE INDEX product_build_runs_status_idx
    ON product_build_runs (status, updated_at DESC);

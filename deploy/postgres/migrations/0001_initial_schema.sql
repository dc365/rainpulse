CREATE TABLE config_versions (
    config_version TEXT PRIMARY KEY,
    sha256 CHAR(64) NOT NULL UNIQUE,
    config JSONB NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE data_sources (
    source_id UUID PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    config_version TEXT REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (source_type IN ('radar', 'qpe', 'gauge', 'nwp'))
);

CREATE TABLE model_versions (
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_type TEXT NOT NULL,
    config_version TEXT NOT NULL REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    artifact_uri TEXT,
    artifact_sha256 CHAR(64),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (model_id, model_version),
    CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE input_assets (
    asset_id UUID PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES data_sources(source_id) ON DELETE RESTRICT,
    issue_time TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ,
    object_uri TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    sha256 CHAR(64) NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'registered',
    quality_state TEXT NOT NULL DEFAULT 'unknown',
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ,
    CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('registered', 'available', 'invalid', 'quarantined', 'deleted')),
    CHECK (quality_state IN ('unknown', 'valid', 'low_quality', 'missing'))
);

CREATE TABLE forecast_runs (
    run_id UUID PRIMARY KEY,
    issue_time TIMESTAMPTZ NOT NULL,
    grid_id TEXT NOT NULL,
    config_version TEXT NOT NULL REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'queued',
    rerun_of UUID REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    reason TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('queued', 'running', 'completed', 'degraded', 'failed', 'cancelled')),
    CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at),
    CHECK (rerun_of IS NULL OR rerun_of <> run_id)
);

CREATE TABLE jobs (
    job_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    job_type TEXT NOT NULL,
    model_id TEXT,
    model_version TEXT,
    config_version TEXT NOT NULL REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'pending',
    max_attempts SMALLINT NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, job_type, model_id, model_version, config_version),
    FOREIGN KEY (model_id, model_version)
        REFERENCES model_versions(model_id, model_version) ON DELETE RESTRICT,
    CHECK ((model_id IS NULL) = (model_version IS NULL)),
    CHECK (status IN ('pending', 'published', 'running', 'retrying', 'completed', 'failed', 'cancelled')),
    CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE job_attempts (
    job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
    attempt_no SMALLINT NOT NULL CHECK (attempt_no > 0),
    status TEXT NOT NULL,
    worker_id TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    PRIMARY KEY (job_id, attempt_no),
    CHECK (status IN ('running', 'completed', 'failed', 'timed_out')),
    CHECK (completed_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE model_runs (
    model_run_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    job_id UUID NOT NULL UNIQUE REFERENCES jobs(job_id) ON DELETE RESTRICT,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    config_version TEXT NOT NULL REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    input_asset_ids UUID[] NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    runtime_ms BIGINT CHECK (runtime_ms IS NULL OR runtime_ms >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    FOREIGN KEY (model_id, model_version)
        REFERENCES model_versions(model_id, model_version) ON DELETE RESTRICT,
    CHECK (cardinality(input_asset_ids) > 0),
    CHECK (status IN ('running', 'completed', 'failed', 'degraded')),
    CHECK (completed_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE products (
    product_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    model_run_id UUID NOT NULL REFERENCES model_runs(model_run_id) ON DELETE RESTRICT,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    config_version TEXT NOT NULL REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    input_asset_ids UUID[] NOT NULL,
    product_type TEXT NOT NULL,
    grid_id TEXT NOT NULL,
    issue_time TIMESTAMPTZ NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'published',
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (model_id, model_version)
        REFERENCES model_versions(model_id, model_version) ON DELETE RESTRICT,
    CHECK (cardinality(input_asset_ids) > 0),
    CHECK (valid_to >= valid_from),
    CHECK (status IN ('publishing', 'published', 'superseded', 'invalid'))
);

CREATE TABLE product_assets (
    product_asset_id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    asset_type TEXT NOT NULL,
    object_uri TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    lead_minutes SMALLINT CHECK (lead_minutes IS NULL OR lead_minutes >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ,
    UNIQUE (product_id, object_uri),
    CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE verification_runs (
    verification_run_id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    truth_asset_id UUID NOT NULL REFERENCES input_assets(asset_id) ON DELETE RESTRICT,
    config_version TEXT NOT NULL REFERENCES config_versions(config_version) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'queued',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_id, truth_asset_id, config_version),
    CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE verification_metrics (
    verification_metric_id UUID PRIMARY KEY,
    verification_run_id UUID NOT NULL REFERENCES verification_runs(verification_run_id) ON DELETE RESTRICT,
    metric_name TEXT NOT NULL,
    lead_minutes SMALLINT NOT NULL CHECK (lead_minutes >= 0),
    threshold DOUBLE PRECISION,
    value DOUBLE PRECISION NOT NULL,
    sample_count BIGINT NOT NULL CHECK (sample_count >= 0),
    dimensions JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (verification_run_id, metric_name, lead_minutes, threshold, dimensions)
);

CREATE TABLE alerts (
    alert_id UUID PRIMARY KEY,
    run_id UUID REFERENCES forecast_runs(run_id) ON DELETE RESTRICT,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    message TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::JSONB,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    acknowledged_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    CHECK (severity IN ('info', 'warning', 'critical')),
    CHECK (status IN ('open', 'acknowledged', 'resolved'))
);

CREATE TABLE outbox_events (
    event_id UUID PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_version SMALLINT NOT NULL DEFAULT 1 CHECK (event_version > 0),
    subject TEXT NOT NULL,
    payload JSONB NOT NULL,
    headers JSONB NOT NULL DEFAULT '{}'::JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('pending', 'publishing', 'published', 'failed'))
);

CREATE INDEX input_assets_issue_time_idx ON input_assets (issue_time DESC);
CREATE INDEX input_assets_source_status_idx ON input_assets (source_id, status, issue_time DESC);
CREATE INDEX forecast_runs_issue_time_idx ON forecast_runs (issue_time DESC, created_at DESC);
CREATE INDEX forecast_runs_status_idx ON forecast_runs (status, updated_at);
CREATE INDEX jobs_run_status_idx ON jobs (run_id, status);
CREATE INDEX jobs_ready_idx ON jobs (status, scheduled_at) WHERE status IN ('pending', 'retrying');
CREATE INDEX products_issue_time_idx ON products (issue_time DESC, product_type, model_id);
CREATE INDEX verification_runs_status_idx ON verification_runs (status, created_at);
CREATE INDEX alerts_open_idx ON alerts (severity, opened_at DESC) WHERE status <> 'resolved';
CREATE INDEX outbox_events_pending_idx ON outbox_events (available_at, created_at)
    WHERE status IN ('pending', 'failed');

COMMENT ON COLUMN input_assets.deleted_at IS
    'Metadata tombstone only; object-store deletion is handled by an explicit retention workflow.';
COMMENT ON COLUMN product_assets.deleted_at IS
    'Metadata tombstone only; object-store deletion is handled by an explicit retention workflow.';

-- Explicit additive migration after schema.sql and schema_v2.sql.
-- Retention only owns s3://rainpulse/operations/<run>/<task>/attempts/<attempt>/.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('rainpulse-operations-schema-v1',0));
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM ops_schema WHERE version=2) THEN RAISE EXCEPTION 'operations schema v2 required'; END IF;
END $$;
CREATE TABLE IF NOT EXISTS ops_release_channels (
 kind text PRIMARY KEY CHECK(kind IN ('qc','render','diagnostics')),
 current_fingerprint text NOT NULL DEFAULT '', previous_fingerprint text NOT NULL DEFAULT '',
 revision bigint NOT NULL DEFAULT 1, actor text NOT NULL DEFAULT 'bootstrap',
 reason text NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO ops_release_channels(kind) VALUES('qc'),('render'),('diagnostics') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS ops_storage_control (
 id integer PRIMARY KEY CHECK(id=1), session_id uuid,
 pressure_report text NOT NULL DEFAULT '', inode_stop_percent integer NOT NULL DEFAULT 95 CHECK(inode_stop_percent BETWEEN 50 AND 99),
 minimum_free_bytes bigint NOT NULL DEFAULT 1073741824 CHECK(minimum_free_bytes>=0),
 report_max_age_seconds integer NOT NULL DEFAULT 180 CHECK(report_max_age_seconds BETWEEN 30 AND 3600),
 revision bigint NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO ops_storage_control(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS ops_storage_reports (
 id text PRIMARY KEY, document jsonb NOT NULL, received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops_retention_pins (
 run_id uuid PRIMARY KEY REFERENCES ops_runs(id), reason text NOT NULL, actor text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops_retention_plans (
 id uuid PRIMARY KEY, document jsonb NOT NULL, digest text NOT NULL,
 state text NOT NULL DEFAULT 'PREVIEW' CHECK(state IN ('PREVIEW','DELETING','COMPLETE','ERROR')),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 started_at timestamptz, finished_at timestamptz, receipt jsonb
);
CREATE TABLE IF NOT EXISTS ops_retired_runs (
 run_id uuid PRIMARY KEY REFERENCES ops_runs(id), plan_id uuid NOT NULL REFERENCES ops_retention_plans(id),
 state text NOT NULL DEFAULT 'RETIRED' CHECK(state IN ('RETIRED','DELETED')),
 retired_at timestamptz NOT NULL DEFAULT now(), deleted_at timestamptz
);
CREATE TABLE IF NOT EXISTS ops_storage_events (
 id bigserial PRIMARY KEY, event text NOT NULL, actor text NOT NULL,
 document jsonb NOT NULL, at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ops_retention_plan_state ON ops_retention_plans(state,created_at DESC);
CREATE INDEX IF NOT EXISTS ops_attempts_run_gc ON ops_attempts(task_id,finished_at);
CREATE INDEX IF NOT EXISTS ops_release_fingerprint ON ops_tasks((spec#>>'{identity,fingerprint}'));
INSERT INTO ops_schema(version) VALUES(3) ON CONFLICT DO NOTHING;
COMMIT;

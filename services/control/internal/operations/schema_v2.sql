-- Run after schema.sql, before starting the v2 API / management Workers.
-- Stop new management submissions and drain old frozen attempts first.
-- Never infer a historical enqueue timestamp from created_at/dispatched_at.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('rainpulse-operations-schema-v1',0));
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM ops_schema WHERE version=1) THEN
  RAISE EXCEPTION 'operations schema v1 is required';
 END IF;
END $$;
ALTER TABLE ops_tasks ADD COLUMN IF NOT EXISTS queued_at timestamptz;
ALTER TABLE ops_attempts ADD COLUMN IF NOT EXISTS queued_at timestamptz;
CREATE TABLE IF NOT EXISTS ops_pool_controls (
 kind text PRIMARY KEY CHECK(kind IN ('qc','render','diagnostics')),
 mode text NOT NULL DEFAULT 'ACCEPTING' CHECK(mode IN ('ACCEPTING','DRAINING')),
 revision bigint NOT NULL DEFAULT 1,
 updated_at timestamptz NOT NULL DEFAULT now(), actor text NOT NULL DEFAULT 'bootstrap',
 reason text NOT NULL DEFAULT ''
);
INSERT INTO ops_pool_controls(kind) VALUES ('qc'),('render'),('diagnostics') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS ops_resource_events (
 id bigserial PRIMARY KEY, kind text NOT NULL REFERENCES ops_pool_controls(kind),
 action text NOT NULL, revision bigint NOT NULL, actor text NOT NULL, reason text NOT NULL,
 at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ops_resource_events_page ON ops_resource_events(id DESC);
CREATE TABLE IF NOT EXISTS ops_asset_checks (
 scan_id uuid NOT NULL, stage text NOT NULL CHECK(stage IN ('normalized','qc','grid')),
 uri text NOT NULL, state text NOT NULL CHECK(state IN ('marker_checked','unverified')),
 checked_at timestamptz NOT NULL DEFAULT now(), result jsonb NOT NULL,
 PRIMARY KEY(scan_id,stage)
);
CREATE INDEX IF NOT EXISTS ops_attempts_started_page ON ops_attempts(started_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ops_attempts_live_worker ON ops_attempts(worker_id) WHERE state IN ('RUNNING','COMMITTING');
CREATE INDEX IF NOT EXISTS ops_tasks_input_scan ON ops_tasks((spec#>>'{request,payload,scan_id}'),created_at DESC,id DESC);
INSERT INTO ops_schema(version) VALUES(2) ON CONFLICT DO NOTHING;
COMMIT;

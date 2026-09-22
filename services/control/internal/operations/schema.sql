-- Apply explicitly with psql -X -v ON_ERROR_STOP=1 -f this-file.
-- Additive only; no legacy task state/asset pointer is migrated or rewritten.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('rainpulse-operations-schema-v1',0));
CREATE TABLE IF NOT EXISTS ops_schema(version integer PRIMARY KEY, installed_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ops_plans (
 id uuid PRIMARY KEY, run_id uuid NOT NULL UNIQUE, document jsonb NOT NULL,
 digest text NOT NULL, expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL,
 submitted_at timestamptz, idempotency_key text UNIQUE
);
CREATE TABLE IF NOT EXISTS ops_runs (
 id uuid PRIMARY KEY, plan_id uuid NOT NULL UNIQUE REFERENCES ops_plans(id),
 name text NOT NULL, mode text NOT NULL DEFAULT 'ACTIVE' CHECK(mode IN('ACTIVE','PAUSED','CANCELLED')),
 state text NOT NULL DEFAULT 'QUEUED', actor text NOT NULL, impact text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops_tasks (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES ops_runs(id),
 parent_id uuid REFERENCES ops_tasks(id), ordinal integer NOT NULL, kind text NOT NULL,
 spec jsonb NOT NULL, state text NOT NULL CHECK(state IN('WAITING','QUEUED','RUNNING','COMMITTING','SUCCEEDED','FAILED','BLOCKED','CANCELLED')),
 generation integer NOT NULL DEFAULT 1, attempt_no integer NOT NULL DEFAULT 0,
 current_attempt uuid, error_code text NOT NULL DEFAULT '', error_message text NOT NULL DEFAULT '',
 result jsonb, dispatched_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(run_id,ordinal)
);
CREATE TABLE IF NOT EXISTS ops_attempts (
 id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES ops_tasks(id), number integer NOT NULL,
 worker_id text NOT NULL, token_sha256 text NOT NULL, state text NOT NULL, stage text NOT NULL,
 request jsonb NOT NULL, started_at timestamptz NOT NULL, heartbeat_at timestamptz NOT NULL,
 lease_until timestamptz NOT NULL, finished_at timestamptz, result jsonb,
 metrics jsonb NOT NULL DEFAULT '{}'::jsonb, UNIQUE(task_id,number)
);
CREATE TABLE IF NOT EXISTS ops_workers (
 id text PRIMARY KEY, identity jsonb NOT NULL, kind text NOT NULL, fingerprint text NOT NULL,
 seen_at timestamptz NOT NULL, ready boolean NOT NULL, busy boolean NOT NULL, current_task text NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ops_events (
 id bigserial PRIMARY KEY, run_id uuid NOT NULL REFERENCES ops_runs(id),
 task_id uuid REFERENCES ops_tasks(id), attempt_id uuid REFERENCES ops_attempts(id),
 sequence bigint, level text NOT NULL, event text NOT NULL, message text NOT NULL,
 at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ops_events_attempt_sequence ON ops_events(attempt_id,sequence) WHERE sequence IS NOT NULL;
CREATE INDEX IF NOT EXISTS ops_tasks_run_order ON ops_tasks(run_id,ordinal);
CREATE INDEX IF NOT EXISTS ops_tasks_parent ON ops_tasks(parent_id);
CREATE INDEX IF NOT EXISTS ops_runs_page ON ops_runs(created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ops_runs_state_page ON ops_runs(state,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ops_events_run_page ON ops_events(run_id,id);
CREATE INDEX IF NOT EXISTS ops_events_task_page ON ops_events(task_id,id);
CREATE INDEX IF NOT EXISTS ops_workers_matching ON ops_workers(kind,fingerprint,seen_at DESC);
INSERT INTO ops_schema(version) VALUES(1) ON CONFLICT DO NOTHING;
COMMIT;

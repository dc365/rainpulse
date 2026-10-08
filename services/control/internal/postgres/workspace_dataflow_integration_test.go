package postgres

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Uses session-local temporary tables, never production records. Validates the
// dataflow scan join (window predicate, job grouping, attempt error lift)
// against a real PostgreSQL engine.
func TestDataflowScanRowsJoinsJobsAndRespectsWindow(t *testing.T) {
	dsn := os.Getenv("RAINPULSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set RAINPULSE_TEST_DATABASE_URL for PostgreSQL integration")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	// TEMP tables are session-local: pin the pool to a single connection.
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	config.MaxConns = 1
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	if _, err := pool.Exec(ctx, `
CREATE TEMP TABLE radar_scans (
    scan_id uuid PRIMARY KEY, radar_id text NOT NULL,
    volume_start_time timestamptz NOT NULL, volume_end_time timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (radar_id, volume_start_time, volume_end_time));
CREATE TEMP TABLE radar_scan_runs (
    run_id uuid PRIMARY KEY, scan_id uuid NOT NULL UNIQUE, radar_id text NOT NULL,
    radar_config_version text NOT NULL DEFAULT 'v1', status text NOT NULL,
    degraded_reason text, normalized_uri text, qc_uri text, grid_uri text,
    scan_completeness double precision, mean_quality_index double precision,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TEMP TABLE jobs (
    job_id uuid PRIMARY KEY, run_id uuid NOT NULL, trace_id uuid,
    job_type text NOT NULL, model_id text, model_version text,
    config_version text NOT NULL DEFAULT 'v1', status text NOT NULL,
    max_attempts smallint NOT NULL DEFAULT 3, scheduled_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at timestamptz, completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    request_payload jsonb NOT NULL DEFAULT '{}');
CREATE TEMP TABLE job_attempts (
    job_id uuid NOT NULL, attempt_no smallint NOT NULL, status text NOT NULL,
    worker_id text, error_code text, error_message text,
    started_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamptz, metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (job_id, attempt_no));`); err != nil {
		t.Fatal(err)
	}

	now := time.Date(2026, 10, 8, 4, 30, 0, 0, time.UTC)
	inside := now.Add(-6 * time.Minute)
	outside := now.Add(-90 * time.Minute)
	if _, err := pool.Exec(ctx, `
INSERT INTO radar_scans (scan_id, radar_id, volume_start_time, volume_end_time, received_at) VALUES
 ('00000000-0000-0000-0000-0000000000a1','z9591',$1,$2,$3),
 ('00000000-0000-0000-0000-0000000000b1','z9591',$4,$5,$6),
 ('00000000-0000-0000-0000-0000000000c1','z9593',$1,$2,$3),
 ('00000000-0000-0000-0000-0000000000d1','z9593',$10,$11,$12);
INSERT INTO radar_scan_runs (run_id, scan_id, radar_id, status, scan_completeness, mean_quality_index) VALUES
 ('00000000-0000-0000-0000-0000000000a2','00000000-0000-0000-0000-0000000000a1','z9591','QC_RUNNING',0.99,0.9),
 ('00000000-0000-0000-0000-0000000000b2','00000000-0000-0000-0000-0000000000b1','z9591','RADAR_GRID_READY',NULL,NULL),
 ('00000000-0000-0000-0000-0000000000c2','00000000-0000-0000-0000-0000000000c1','z9593','RADAR_GRID_READY',NULL,NULL),
 ('00000000-0000-0000-0000-0000000000d2','00000000-0000-0000-0000-0000000000d1','z9593','RAW_RECEIVED',NULL,NULL);
INSERT INTO jobs (job_id, run_id, job_type, status, created_at, started_at, completed_at) VALUES
 ('00000000-0000-0000-0000-0000000000a3','00000000-0000-0000-0000-0000000000a2','radar.decode','SUCCEEDED',$7,$8,$9),
 ('00000000-0000-0000-0000-0000000000a4','00000000-0000-0000-0000-0000000000a2','radar.qc','RUNNING',$7,$8,NULL),
 ('00000000-0000-0000-0000-0000000000c3','00000000-0000-0000-0000-0000000000c2','radar.decode','FAILED',$7,$8,$9);
INSERT INTO job_attempts (job_id, attempt_no, status, error_code) VALUES
 ('00000000-0000-0000-0000-0000000000c3',1,'failed','decode_header_invalid');`,
		inside, inside.Add(5*time.Minute), inside.Add(5*time.Minute),
		outside, outside.Add(5*time.Minute), outside.Add(5*time.Minute),
		inside.Add(-6*time.Minute), inside.Add(-6*time.Minute), inside.Add(-5*time.Minute),
		now.Add(30*time.Minute), now.Add(35*time.Minute), now.Add(35*time.Minute)); err != nil {
		t.Fatal(err)
	}

	store := New(pool)
	blocks, err := store.dataflowScanRows(ctx, now.Add(-time.Hour), now)
	if err != nil {
		t.Fatal(err)
	}
	if len(blocks) != 2 {
		t.Fatalf("block count = %d; want 2 (old scan outside window, future scan beyond the anchor)", len(blocks))
	}
	z9591Running := blocks[0]
	if z9591Running.Status != "QC_RUNNING" || len(z9591Running.Stages) != 2 {
		t.Fatalf("z9591 running block = %+v", z9591Running)
	}
	if z9591Running.Stages[0].Stage != "decode" || z9591Running.Stages[0].RuntimeMS == nil {
		t.Fatalf("decode stage = %+v", z9591Running.Stages[0])
	}
	failedScan := blocks[1]
	if len(failedScan.Stages) != 1 || failedScan.Stages[0].ErrorCode != "decode_header_invalid" {
		t.Fatalf("failed scan stages = %+v", failedScan.Stages)
	}
}

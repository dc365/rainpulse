package postgres

import (
	"context"
	"encoding/json"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"os"
	"strings"
	"testing"
	"time"
)

func TestDraftXDecodeRebuildPreservesHistoryAndIsIdempotent(t *testing.T) {
	dsn := os.Getenv("RAINPULSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("isolated PostgreSQL required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	cfg.MaxConns = 1
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	ddl := `CREATE TEMP TABLE config_versions(config_version text PRIMARY KEY,sha256 text,config jsonb,description text,created_at timestamptz);
CREATE TEMP TABLE data_sources(source_id uuid PRIMARY KEY,name text,source_type text,enabled bool,config_version text,metadata jsonb,created_at timestamptz,updated_at timestamptz);
CREATE TEMP TABLE input_assets(asset_id uuid PRIMARY KEY,source_id uuid,issue_time timestamptz,observed_at timestamptz,object_uri text,media_type text,size_bytes bigint,sha256 text,status text,quality_state text,metadata jsonb,created_at timestamptz);
CREATE TEMP TABLE radars(radar_id text PRIMARY KEY,display_name text,lifecycle text,current_config_version text,created_at timestamptz,updated_at timestamptz);
CREATE TEMP TABLE radar_config_versions(radar_id text,radar_config_version text,config jsonb,sha256 text,created_at timestamptz,PRIMARY KEY(radar_id,radar_config_version));
CREATE TEMP TABLE workflow_runs(run_id uuid PRIMARY KEY,run_type text,created_at timestamptz);
CREATE TEMP TABLE radar_scans(scan_id uuid PRIMARY KEY,radar_id text,raw_asset_id uuid,volume_start_time timestamptz,volume_end_time timestamptz,received_at timestamptz,created_at timestamptz);
CREATE TEMP TABLE radar_scan_runs(run_id uuid PRIMARY KEY,scan_id uuid UNIQUE,radar_id text,radar_config_version text,status text,normalized_uri text,qc_uri text,grid_uri text,degraded_reason text,scan_completeness float8,mean_quality_index float8,created_at timestamptz,updated_at timestamptz);
CREATE TEMP TABLE jobs(job_id uuid PRIMARY KEY,run_id uuid,trace_id uuid,job_type text,model_id text,model_version text,config_version text,status text,max_attempts int,scheduled_at timestamptz,created_at timestamptz,updated_at timestamptz,request_payload jsonb);
CREATE TEMP TABLE outbox_events(event_id uuid PRIMARY KEY,aggregate_type text,aggregate_id text,event_type text,event_version int,subject text,payload jsonb,status text,available_at timestamptz,created_at timestamptz);`
	if _, err = pool.Exec(ctx, ddl); err != nil {
		t.Fatal(err)
	}
	service := orchestration.NewService(New(pool), orchestration.Options{})
	input := orchestration.RadarDecodeInput{RadarID: "x1", Lifecycle: workflow.RadarDraft, ConfigVersion: "x-v1", Config: json.RawMessage(`{"hardware":{"radar_band":"X"}}`), ConfigSHA256: strings.Repeat("a", 64), SourceFormat: "cma-rstm-level2", InputURI: "s3://rainpulse/radar/raw/x1/a.bz2", InputSHA256: strings.Repeat("b", 64), InputSizeBytes: 123, VolumeStartTime: time.Now().Add(-time.Hour), VolumeEndTime: time.Now().Add(-59 * time.Minute)}
	scan, old, err := service.CreateRadarDecode(ctx, input)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = pool.Exec(ctx, `UPDATE radar_scan_runs SET status='NORMALIZED',normalized_uri='s3://old/volume.zarr'`); err != nil {
		t.Fatal(err)
	}
	input.ExistingRunID = scan.RunID
	input.RebuildID = uuid.New()
	input.ConfigVersion = "x-v2"
	input.ConfigSHA256 = strings.Repeat("c", 64)
	rebuilt, job, err := service.CreateRadarDecode(ctx, input)
	if err != nil {
		t.Fatal(err)
	}
	if rebuilt.ID != scan.ID || rebuilt.RunID != scan.RunID || job.ID == old.ID {
		t.Fatal("scan identity changed or job reused")
	}
	var status, version string
	var normalized *string
	if err = pool.QueryRow(ctx, `SELECT status,radar_config_version,normalized_uri FROM radar_scan_runs`).Scan(&status, &version, &normalized); err != nil {
		t.Fatal(err)
	}
	if status != "RAW_VALIDATING" || version != "x-v2" || normalized != nil {
		t.Fatal("stale normalized asset exposed under new config")
	}
	var n int
	if err = pool.QueryRow(ctx, `SELECT count(*) FROM jobs`).Scan(&n); err != nil || n != 2 {
		t.Fatal("old job was not retained", err, n)
	}
	var request orchestration.RadarDecodeRequested
	if err = json.Unmarshal(job.RequestPayload, &request); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(request.Payload.OutputPrefix, "/rebuilds/") {
		t.Fatal("output collision")
	}
	if _, _, err = service.CreateRadarDecode(ctx, input); err != nil {
		t.Fatal("same request not idempotent", err)
	}
	input.RebuildID = uuid.New()
	if _, _, err = service.CreateRadarDecode(ctx, input); err == nil {
		t.Fatal("concurrent rebuild accepted")
	}
	if _, err = pool.Exec(ctx, `UPDATE radar_scan_runs SET status='NORMALIZED',normalized_uri='s3://new/volume.zarr'`); err != nil {
		t.Fatal(err)
	}
	input.RebuildID = request.Payload.ScanID // distinct stable test token
	input.InputSHA256 = strings.Repeat("d", 64)
	if _, _, err = service.CreateRadarDecode(ctx, input); err == nil {
		t.Fatal("changed raw input accepted")
	}
	input.InputSHA256 = strings.Repeat("b", 64)
	input.Lifecycle = workflow.RadarReady
	if _, _, err = service.CreateRadarDecode(ctx, input); err == nil {
		t.Fatal("trusted radar rebuild accepted")
	}
}

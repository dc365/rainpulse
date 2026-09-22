package postgres

// Opt-in tests execute real SQL in session-local temporary tables. Never point
// this test at production. No permanent schema or existing data is changed.
import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/planning"
	"github.com/jackc/pgx/v5/pgxpool"
)

func batch1Database(t *testing.T) (*Store, context.Context) {
	t.Helper()
	url := os.Getenv("RAINPULSE_TEST_DATABASE_URL")
	if url == "" {
		t.Skip("set RAINPULSE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	t.Cleanup(cancel)
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		t.Fatal(err)
	}
	cfg.MaxConns = 1
	cfg.MinConns = 1
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	ddl := []string{
		`CREATE TEMP TABLE radar_scans(scan_id uuid,radar_id text,volume_start_time timestamptz,volume_end_time timestamptz,received_at timestamptz)`,
		`CREATE TEMP TABLE radar_scan_runs(run_id uuid,scan_id uuid,radar_config_version text,status text,degraded_reason text,normalized_uri text,qc_uri text,grid_uri text,scan_completeness double precision,mean_quality_index double precision,created_at timestamptz,updated_at timestamptz)`,
		`CREATE TEMP TABLE qc_batch_items(kind text,item_id uuid)`,
		`CREATE TEMP TABLE analysis_cycles(analysis_id uuid,run_id uuid,analysis_time timestamptz,grid_id text,config_version text,status text,degraded_reason text,radar_count integer,valid_coverage_ratio double precision,mean_quality_index double precision,mosaic_uri text,analysis_uri text,created_at timestamptz,updated_at timestamptz)`,
		`CREATE TEMP TABLE mosaic_runs(analysis_id uuid,job_id uuid)`,
		`CREATE TEMP TABLE jobs(job_id uuid,regeneration_request_id uuid)`,
		`CREATE TEMP TABLE forecast_runs(run_id uuid,issue_time timestamptz,grid_id text,config_version text,status text,rerun_of uuid,reason text,created_at timestamptz,updated_at timestamptz)`,
	}
	for _, statement := range ddl {
		if _, err := pool.Exec(ctx, statement); err != nil {
			t.Fatal(err)
		}
	}
	return New(pool), ctx
}

func TestBatch1PostgresRadarKeysetAndQCOnly(t *testing.T) {
	store, ctx := batch1Database(t)
	at := time.Date(2026, 9, 22, 0, 0, 0, 0, time.UTC)
	exec := func(sql string, args ...any) {
		t.Helper()
		if _, err := store.pool.Exec(ctx, sql, args...); err != nil {
			t.Fatal(err)
		}
	}
	exec(`INSERT INTO radar_scans SELECT md5('scan'||i)::uuid,CASE WHEN i=453 THEN 'other' ELSE 'Z9598' END,$1,$1,$1 FROM generate_series(1,453) i`, at)
	exec(`INSERT INTO radar_scan_runs SELECT md5('run'||i)::uuid,md5('scan'||i)::uuid,'config','QC_READY',NULL,NULL,NULL,NULL,1,1,$1,$1 FROM generate_series(1,453) i`, at)
	exec(`INSERT INTO qc_batch_items VALUES('qc',md5('scan452')::uuid)`)
	scope := planning.Scope{Status: "QC_READY", RadarIDs: []string{"z9598"}, ExcludeQCOnly: true, Windows: []planning.Window{{Start: at.Add(-time.Minute), End: at.Add(time.Minute)}}}
	var cursor *planning.Cursor
	seen := map[string]bool{}
	for pages := 0; ; pages++ {
		if pages > 10 {
			t.Fatal("pagination did not terminate")
		}
		items, err := store.ListPlannerRadarScans(ctx, scope, cursor)
		if err != nil {
			t.Fatal(err)
		}
		for _, item := range items {
			id := item.RunID.String()
			if seen[id] {
				t.Fatal("duplicate workflow")
			}
			seen[id] = true
		}
		if len(items) < planning.PageSize {
			break
		}
		last := items[len(items)-1]
		cursor = &planning.Cursor{Time: last.VolumeEndTime, ID: last.RunID.String()}
		// Rows leave the stage between pages; OFFSET would skip remaining work.
		for _, item := range items {
			exec(`UPDATE radar_scan_runs SET status='RADAR_GRID_READY' WHERE run_id=$1`, item.RunID)
		}
	}
	if len(seen) != 451 {
		t.Fatalf("visited %d workflows, want 451", len(seen))
	}
}

func TestBatch1PostgresAnalysisFiltersBeforeLimit(t *testing.T) {
	store, ctx := batch1Database(t)
	at := time.Date(2026, 9, 22, 0, 0, 0, 0, time.UTC)
	_, err := store.pool.Exec(ctx, `INSERT INTO analysis_cycles SELECT md5('analysis'||i)::uuid,md5('arun'||i)::uuid,$1,CASE WHEN i>452 THEN 'other' ELSE 'grid' END,'config','ANALYSIS_READY',NULL,1,1,1,NULL,NULL,$1,$1 FROM generate_series(1,900) i`, at)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = store.pool.Exec(ctx, `INSERT INTO mosaic_runs VALUES(md5('analysis452')::uuid,md5('job')::uuid)`); err != nil {
		t.Fatal(err)
	}
	if _, err = store.pool.Exec(ctx, `INSERT INTO jobs VALUES(md5('job')::uuid,md5('regeneration')::uuid)`); err != nil {
		t.Fatal(err)
	}
	scope := planning.Scope{Status: "ANALYSIS_READY", GridID: "grid", Windows: []planning.Window{{Start: at.Add(-time.Minute), End: at.Add(time.Minute)}}}
	var cursor *planning.Cursor
	count := 0
	for {
		items, err := store.ListPlannerAnalyses(ctx, scope, cursor)
		if err != nil {
			t.Fatal(err)
		}
		count += len(items)
		if len(items) < planning.PageSize {
			break
		}
		last := items[len(items)-1]
		cursor = &planning.Cursor{Time: last.AnalysisTime, ID: last.ID.String()}
		if count > 1000 {
			t.Fatal("pagination did not terminate")
		}
	}
	if count != 451 {
		t.Fatalf("analysis count=%d, want451", count)
	}
}

func TestBatch1PostgresForecastRerunAndTimestampTies(t *testing.T) {
	store, ctx := batch1Database(t)
	at := time.Date(2026, 9, 22, 0, 0, 0, 0, time.UTC)
	_, err := store.pool.Exec(ctx, `INSERT INTO forecast_runs SELECT md5('forecast'||i)::uuid,$1,'grid','config','INPUT_READY',NULL,NULL,$1,$1 FROM generate_series(1,451) i`, at)
	if err != nil {
		t.Fatal(err)
	}
	_, err = store.pool.Exec(ctx, `INSERT INTO forecast_runs VALUES(md5('manual')::uuid,$1,'grid','config','INPUT_READY',md5('parent')::uuid,NULL,$1,$1),(md5('old')::uuid,$1,'grid','config','INPUT_READY',NULL,NULL,$1,$1)`, at.Add(-24*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	scope := planning.Scope{Status: "INPUT_READY", GridID: "grid", Windows: []planning.Window{{Start: at.Add(-time.Minute), End: at.Add(time.Minute)}}}
	for _, includeReruns := range []bool{false, true} {
		scope.IncludeReruns = includeReruns
		var cursor *planning.Cursor
		count := 0
		for {
			items, err := store.ListPlannerRuns(ctx, scope, cursor)
			if err != nil {
				t.Fatal(err)
			}
			count += len(items)
			if len(items) < planning.PageSize {
				break
			}
			last := items[len(items)-1]
			cursor = &planning.Cursor{Time: last.IssueTime, ID: last.ID.String()}
			if count > 1000 {
				t.Fatal("pagination did not terminate")
			}
		}
		expected := 451
		if includeReruns {
			expected++
		}
		if count != expected {
			t.Fatalf("reruns=%v count=%d want=%d", includeReruns, count, expected)
		}
	}
}

package operations

import (
	"context"
	"github.com/jackc/pgx/v5"
	"os"
	"testing"
	"time"
)

// Session-local tables keep this regression independent of operational data.
func TestCompositeTimelineFullDayLatestAttempt(t *testing.T) {
	dsn := os.Getenv("RAINPULSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires isolated PostgreSQL test database")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(ctx)
	_, err = conn.Exec(ctx, `CREATE TEMP TABLE ops_tasks(id text,current_attempt text,spec jsonb,kind text,state text,result jsonb,updated_at timestamptz,run_id text);
 CREATE TEMP TABLE ops_retired_runs(run_id text);
 INSERT INTO ops_tasks SELECT i::text,'old',jsonb_build_object('request',jsonb_build_object('payload',jsonb_build_object('mode','sx_composite','analysis_time',('2026-08-28 00:00Z'::timestamptz+i*interval '6 minutes')::text))),'multiband','SUCCEEDED','{"asset":{"uri":"test"}}','2026-09-01','active' FROM generate_series(0,239) i;
 INSERT INTO ops_tasks SELECT id,'new',spec,kind,state,result,'2026-09-02',run_id FROM ops_tasks;
 INSERT INTO ops_tasks SELECT id,'retired',spec,kind,state,result,'2026-09-03','retired' FROM ops_tasks WHERE current_attempt='new';
 INSERT INTO ops_retired_runs VALUES ('retired');`)
	if err != nil {
		t.Fatal(err)
	}
	rows, err := conn.Query(ctx, compositeTimelineSQL, "2026-08-28T00:00:00Z", "2026-08-29T00:00:00Z")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	count := 0
	seen := map[string]bool{}
	for rows.Next() {
		var id, at string
		if err = rows.Scan(&id, &at); err != nil {
			t.Fatal(err)
		}
		if seen[at] || len(id) < 4 || id[len(id)-4:] != ".new" {
			t.Fatalf("wrong version/duplicate: %s %s", id, at)
		}
		seen[at] = true
		count++
	}
	if rows.Err() != nil {
		t.Fatal(rows.Err())
	}
	if count != 240 {
		t.Fatalf("got %d frames, want 240", count)
	}
}

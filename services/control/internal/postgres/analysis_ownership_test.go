package postgres

import (
	"context"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"os"
	"testing"
	"time"
)

func TestAutomaticAnalysisSQLExcludesRegenerationBeforePagination(t *testing.T) {
	dsn := os.Getenv("RAINPULSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set RAINPULSE_TEST_DATABASE_URL for PostgreSQL integration")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(ctx)
	_, err = conn.Exec(ctx, `CREATE TEMP TABLE analysis_cycles (analysis_id uuid, analysis_time timestamptz, created_at timestamptz, status text);
 CREATE TEMP TABLE mosaic_runs (analysis_id uuid, job_id uuid);
 CREATE TEMP TABLE jobs (job_id uuid, regeneration_request_id uuid);
 INSERT INTO analysis_cycles SELECT md5(i::text)::uuid, '2026-08-28 08:45Z', '2026-09-11 00:00Z', 'ANALYSIS_QPE' FROM generate_series(1,201) i;
 INSERT INTO mosaic_runs SELECT analysis_id, analysis_id FROM analysis_cycles;
 INSERT INTO jobs SELECT analysis_id, md5('request')::uuid FROM analysis_cycles;
 INSERT INTO analysis_cycles VALUES (md5('ordinary')::uuid, '2026-08-28 00:10Z', '2026-09-04 00:00Z', 'ANALYSIS_QPE');`)
	if err != nil {
		t.Fatal(err)
	}
	for _, status := range []string{"ANALYSIS_QPE", "ANALYSIS_READY"} {
		if _, err = conn.Exec(ctx, "UPDATE analysis_cycles SET status=$1", status); err != nil {
			t.Fatal(err)
		}
		rows, err := conn.Query(ctx, `SELECT a.analysis_id FROM analysis_cycles a`+automaticAnalysisPagePredicate(), status, 200, true, time.Time{}, time.Time{}, uuid.Nil)
		if err != nil {
			t.Fatal(err)
		}
		var got []uuid.UUID
		for rows.Next() {
			var id uuid.UUID
			if err = rows.Scan(&id); err != nil {
				t.Fatal(err)
			}
			got = append(got, id)
		}
		rows.Close()
		if rows.Err() != nil {
			t.Fatal(rows.Err())
		}
		if len(got) != 1 {
			t.Fatalf("%s: got %d automatic cycles; regeneration consumed page or escaped ownership", status, len(got))
		}
	}
}

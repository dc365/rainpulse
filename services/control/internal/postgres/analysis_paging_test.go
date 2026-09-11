package postgres

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Uses a session-local temporary table, never production records. Can also run
// inside the PostgreSQL test container using its local Unix socket.
func TestAnalysisKeysetSQLKeepsTiedVersionsAndOlderTimes(t *testing.T) {
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
	_, err = conn.Exec(ctx, `CREATE TEMP TABLE analysis_cycles (analysis_id uuid PRIMARY KEY, analysis_time timestamptz, created_at timestamptz, status text);
 INSERT INTO analysis_cycles SELECT md5(i::text)::uuid, '2026-08-28 08:45Z', '2026-09-08 00:00Z', 'ANALYSIS_READY' FROM generate_series(1,201) i;
 INSERT INTO analysis_cycles VALUES ('00000000-0000-0000-0000-000000000001','2026-08-28 00:10Z','2026-09-08 00:00Z','ANALYSIS_READY');`)
	if err != nil {
		t.Fatal(err)
	}
	seen := map[uuid.UUID]bool{}
	var at, created time.Time
	var id uuid.UUID
	for page := 0; page < 3; page++ {
		rows, err := conn.Query(ctx, `SELECT a.analysis_id, a.analysis_time, a.created_at FROM analysis_cycles a`+analysisPagePredicate, "ANALYSIS_READY", 200, page == 0, at, created, id)
		if err != nil {
			t.Fatal(err)
		}
		count := 0
		for rows.Next() {
			if err := rows.Scan(&id, &at, &created); err != nil {
				t.Fatal(err)
			}
			if seen[id] {
				t.Fatalf("duplicate across page boundary: %s", id)
			}
			seen[id] = true
			count++
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			t.Fatal(err)
		}
		if count < 200 {
			break
		}
	}
	if len(seen) != 202 || !seen[uuid.MustParse("00000000-0000-0000-0000-000000000001")] {
		t.Fatalf("got %d rows; expected all 202 including morning", len(seen))
	}
}

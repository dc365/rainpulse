package postgres

import (
	"context"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workloads"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Only session-local temporary tables are used. Never use a production DSN.
func routeFixture(t *testing.T) (*Store, workflow.OutboxEvent, uuid.UUID) {
	t.Helper()
	dsn := os.Getenv("RAINPULSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("isolated PostgreSQL DSN required; database integration NOT executed")
	}
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	config.MaxConns = 1
	config.MinConns = 0
	config.MaxConnLifetime = 0
	config.MaxConnIdleTime = 0
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	statements := []string{
		`CREATE TEMP TABLE jobs(job_id uuid PRIMARY KEY,run_id uuid,regeneration_request_id uuid,created_at timestamptz)`,
		`CREATE TEMP TABLE outbox_events(event_id uuid PRIMARY KEY,aggregate_type text,aggregate_id text,event_type text,subject text,status text,created_at timestamptz,resource_route_frozen boolean NOT NULL DEFAULT false,resource_route_reason text)`,
		`CREATE TEMP TABLE radar_scan_runs(run_id uuid,scan_id uuid)`,
		`CREATE TEMP TABLE radar_scans(scan_id uuid,volume_end_time timestamptz)`,
		`CREATE TEMP TABLE analysis_cycles(run_id uuid,analysis_time timestamptz)`,
		`CREATE TEMP TABLE forecast_runs(run_id uuid,issue_time timestamptz,rerun_of uuid)`,
	}
	for _, sql := range statements {
		if _, err = pool.Exec(ctx, sql); err != nil {
			t.Fatal(err)
		}
	}
	job, run, scan, eventID := uuid.New(), uuid.New(), uuid.New(), uuid.New()
	at := time.Date(2026, 9, 22, 10, 0, 0, 0, time.UTC)
	if _, err = pool.Exec(ctx, `INSERT INTO jobs VALUES($1,$2,NULL,$3)`, job, run, at); err != nil {
		t.Fatal(err)
	}
	if _, err = pool.Exec(ctx, `INSERT INTO radar_scan_runs VALUES($1,$2)`, run, scan); err != nil {
		t.Fatal(err)
	}
	if _, err = pool.Exec(ctx, `INSERT INTO radar_scans VALUES($1,$2)`, scan, at.Add(-5*time.Minute)); err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(map[string]any{"job_id": job, "event_type": "radar.qc.requested.v1"})
	event := workflow.OutboxEvent{ID: eventID, AggregateID: job.String(), EventType: "radar.qc.requested.v1", Subject: workloads.RequestPrefix + "radar_qc", Payload: body}
	if _, err = pool.Exec(ctx, `INSERT INTO outbox_events(event_id,aggregate_type,aggregate_id,event_type,subject,status,created_at) VALUES($1,'job',$2,$3,$4,'publishing',$5)`, eventID, job.String(), event.EventType, event.Subject, at); err != nil {
		t.Fatal(err)
	}
	return New(pool), event, job
}

func TestBatch2PostgresFreshRouteIsFrozenAcrossPolicyChange(t *testing.T) {
	store, event, _ := routeFixture(t)
	ctx := context.Background()
	settings := workloads.Settings{Enabled: true, RealtimeAge: time.Hour, FutureSkew: 6 * time.Minute}
	first, err := store.RouteRequestedEvent(ctx, event, settings)
	if err != nil || first.Subject != event.Subject {
		t.Fatalf("%+v %v", first, err)
	}
	settings.RealtimeAge = time.Second
	second, err := store.RouteRequestedEvent(ctx, event, settings)
	if err != nil || second.Subject != first.Subject {
		t.Fatalf("frozen route changed: %+v %v", second, err)
	}
	var frozen bool
	if err := store.pool.QueryRow(ctx, `SELECT resource_route_frozen FROM outbox_events WHERE event_id=$1`, event.ID).Scan(&frozen); err != nil || !frozen {
		t.Fatalf("not persisted %v", err)
	}
}

func TestBatch2PostgresRegenerationAndReplayKeepBackground(t *testing.T) {
	store, event, job := routeFixture(t)
	ctx := context.Background()
	if _, err := store.pool.Exec(ctx, `UPDATE jobs SET regeneration_request_id=$1 WHERE job_id=$2`, uuid.New(), job); err != nil {
		t.Fatal(err)
	}
	settings := workloads.Settings{Enabled: true, RealtimeAge: time.Hour, FutureSkew: 6 * time.Minute}
	first, err := store.RouteRequestedEvent(ctx, event, settings)
	if err != nil || first.Subject != workloads.BackgroundPrefix+"radar_qc" {
		t.Fatalf("%+v %v", first, err)
	}
	replay := event
	replay.ID = uuid.New()
	again, err := store.RouteRequestedEvent(ctx, replay, workloads.Settings{})
	if err != nil || again.Subject != first.Subject || again.ID != replay.ID {
		t.Fatalf("retry changed identity: %+v %v", again, err)
	}
	subject, err := store.RequestedRouteForReplay(ctx, job, event.EventType)
	if err != nil || subject != first.Subject {
		t.Fatalf("%s %v", subject, err)
	}
}

func TestBatch2PostgresPublishedLegacyRequestDoesNotMoveQueues(t *testing.T) {
	store, event, job := routeFixture(t)
	ctx := context.Background()
	if _, err := store.pool.Exec(ctx, `UPDATE jobs SET regeneration_request_id=$1 WHERE job_id=$2`, uuid.New(), job); err != nil {
		t.Fatal(err)
	}
	if _, err := store.pool.Exec(ctx, `UPDATE outbox_events SET status='published'`); err != nil {
		t.Fatal(err)
	}
	got, err := store.RouteRequestedEvent(ctx, event, workloads.Settings{Enabled: true})
	if err != nil || got.Subject != event.Subject {
		t.Fatalf("legacy migrated: %+v %v", got, err)
	}
}

func TestBatch2PostgresMismatchedCanonicalStageIsRejected(t *testing.T) {
	store, event, _ := routeFixture(t)
	event.Subject = workloads.RequestPrefix + "radar_grid"
	if _, err := store.RouteRequestedEvent(context.Background(), event, workloads.Settings{Enabled: true}); err == nil {
		t.Fatal("wrong canonical stage accepted")
	}
}

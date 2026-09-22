//go:build integration

package operations

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
)

// These tests require an explicitly opted-in disposable PostgreSQL database.
// All tables live in a uniquely named schema, not the production/public schema.
func integrationStore(t *testing.T) *Store {
	t.Helper()
	dsn := os.Getenv("RAINPULSE_OPS_TEST_DATABASE_URL")
	if dsn == "" || os.Getenv("RAINPULSE_OPS_ALLOW_INTEGRATION") != "1" {
		t.Skip("requires disposable RAINPULSE_OPS_TEST_DATABASE_URL and RAINPULSE_OPS_ALLOW_INTEGRATION=1")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid test database configuration")
	}
	admin := stdlib.OpenDB(*cfg)
	t.Cleanup(func() { _ = admin.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	schema := "ops_test_" + strings.ReplaceAll(NewID(), "-", "")
	if _, err = admin.ExecContext(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	cfg.RuntimeParams["search_path"] = schema
	db := stdlib.OpenDB(*cfg)
	db.SetMaxOpenConns(8)
	t.Cleanup(func() {
		_ = db.Close()
		clean, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_, _ = admin.ExecContext(clean, "DROP SCHEMA "+schema+" CASCADE")
	})
	raw, err := os.ReadFile("schema.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, string(raw)); err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `CREATE TABLE outbox_events(
 event_id uuid PRIMARY KEY,aggregate_type text NOT NULL,aggregate_id text NOT NULL,
 event_type text NOT NULL,event_version integer NOT NULL,subject text NOT NULL,payload jsonb NOT NULL,
 status text NOT NULL,available_at timestamptz NOT NULL,created_at timestamptz NOT NULL)`)
	if err != nil {
		t.Fatal(err)
	}
	return &Store{DB: db}
}
func integrationPlan(t *testing.T, s *Store) Plan {
	t.Helper()
	a, b, c := NewID(), NewID(), NewID()
	qc, render := identity("qc"), identity("render")
	qc.Fingerprint = Digest([]byte("qc"))
	render.Fingerprint = Digest([]byte("render"))
	p := Plan{ID: NewID(), RunID: NewID(), Selection: Selection{Name: "integration"}, Checks: []Check{}, Tasks: []Spec{
		{ID: a, Kind: "qc", Name: "root A", Identity: qc, Request: json.RawMessage(`{"payload":{"b":2,"a":1},"occurred_at":"2026-09-22T00:00:00Z"}`)},
		{ID: b, Kind: "render", ParentID: a, Name: "child A", Identity: render, Request: json.RawMessage(`{"payload":{},"occurred_at":"2026-09-22T00:00:00Z"}`)},
		{ID: c, Kind: "qc", Name: "root B", Identity: qc, Request: json.RawMessage(`{"payload":{},"occurred_at":"2026-09-22T00:00:00Z"}`)}}}
	if err := p.Seal(time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if err := s.SavePlan(ctx, p); err != nil {
		t.Fatal(err)
	}
	for _, i := range []Identity{qc, render} {
		if err := s.Register(ctx, WorkerInfo{ID: "worker-" + i.Kind, Identity: i, Ready: true}); err != nil {
			t.Fatal(err)
		}
	}
	return p
}
func claimed(t *testing.T, s *Store, task, kind string, generation int) Claim {
	t.Helper()
	v, e := s.Claim(context.Background(), task, "worker-"+kind, generation)
	if e != nil || v.Decision != "claimed" {
		t.Fatalf("claim %s: %#v %v", task, v, e)
	}
	return v
}
func finishFixture(t *testing.T, s *Store, c Claim, outcome string) {
	t.Helper()
	p := Finish{Pulse: Pulse{TaskID: c.TaskID, AttemptID: c.AttemptID, Token: c.Token, Stage: "COMMIT"}, Outcome: outcome}
	var result *Candidate
	if outcome == "SUCCEEDED" {
		uri, err := (&Service{}).requestURI(c.Request, c.Kind)
		if err != nil {
			t.Fatal(err)
		}
		result = &Candidate{Asset: AssetRef{URI: uri, SHA256: strings.Repeat("a", 64), MarkerSHA256: strings.Repeat("b", 64), SizeBytes: 16}, CandidateOnly: true}
	}
	if err := s.Finish(context.Background(), p, result, false); err != nil {
		t.Fatal(err)
	}
}
func TestOpsPostgresPlanJSONBRoundTripAndConcurrentSubmit(t *testing.T) {
	s := integrationStore(t)
	p := integrationPlan(t, s)
	ctx := context.Background()
	saved, err := s.Plan(ctx, p.ID)
	if err != nil {
		t.Fatal(err)
	}
	digest := saved.Digest
	saved.Digest = ""
	if CanonicalDigest(saved) != digest {
		t.Fatal("jsonb changed immutable plan digest")
	}
	var wg sync.WaitGroup
	errs := make(chan error, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			id, e := s.Submit(ctx, p, "same-key-for-eight-submitters", "test")
			if e == nil && id != p.RunID {
				t.Error("different run on duplicate")
			}
			errs <- e
		}()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		if e != nil {
			t.Fatal(e)
		}
	}
	var n int
	if err = s.DB.QueryRow(`SELECT count(*) FROM ops_runs`).Scan(&n); err != nil || n != 1 {
		t.Fatal(n, err)
	}
	if err = s.DB.QueryRow(`SELECT count(*) FROM outbox_events`).Scan(&n); err != nil || n != 2 {
		t.Fatal("duplicate root outbox", n, err)
	}
}
func TestOpsPostgresClaimFencingAndPartialFailureRetry(t *testing.T) {
	s := integrationStore(t)
	p := integrationPlan(t, s)
	ctx := context.Background()
	if _, e := s.Submit(ctx, p, NewID(), "test"); e != nil {
		t.Fatal(e)
	}
	a := claimed(t, s, p.Tasks[0].ID, "qc", 1)
	other, e := s.Claim(ctx, a.TaskID, "worker-qc", 1)
	if e != nil || other.Decision != "busy" {
		t.Fatal(other, e)
	}
	finishFixture(t, s, a, "SUCCEEDED")
	child := claimed(t, s, p.Tasks[1].ID, "render", 1)
	finishFixture(t, s, child, "FAILED")
	c := claimed(t, s, p.Tasks[2].ID, "qc", 1)
	finishFixture(t, s, c, "SUCCEEDED")
	run, e := s.Run(ctx, p.RunID)
	if e != nil || run.State != "PARTIAL_SUCCESS" {
		t.Fatal(run, e)
	}
	if e = s.Action(ctx, p.RunID, "retry_failed", "test"); e != nil {
		t.Fatal(e)
	}
	aTask, e := s.Task(ctx, a.TaskID)
	if e != nil || aTask.AttemptNo != 1 || aTask.State != "SUCCEEDED" {
		t.Fatal("success was rerun", aTask, e)
	}
	retried := claimed(t, s, child.TaskID, "render", 2)
	stale := Finish{Pulse: Pulse{TaskID: child.TaskID, AttemptID: child.AttemptID, Token: child.Token, Stage: "COMMIT"}, Outcome: "SUCCEEDED"}
	if e = s.Finish(ctx, stale, nil, false); e == nil {
		t.Fatal("stale result accepted")
	}
	if _, e = s.Heartbeat(ctx, stale.Pulse); e == nil {
		t.Fatal("old heartbeat accepted")
	}
	finishFixture(t, s, retried, "SUCCEEDED")
	run, e = s.Run(ctx, p.RunID)
	if e != nil || run.State != "SUCCEEDED" {
		t.Fatal(run, e)
	}
}
func TestOpsPostgresCancelStopsChildrenAndPreservesAttempts(t *testing.T) {
	s := integrationStore(t)
	p := integrationPlan(t, s)
	ctx := context.Background()
	if _, e := s.Submit(ctx, p, NewID(), "test"); e != nil {
		t.Fatal(e)
	}
	a := claimed(t, s, p.Tasks[0].ID, "qc", 1)
	if e := s.Action(ctx, p.RunID, "cancel", "test"); e != nil {
		t.Fatal(e)
	}
	stop, e := s.Heartbeat(ctx, Pulse{TaskID: a.TaskID, AttemptID: a.AttemptID, Token: a.Token, Stage: "COMPUTE"})
	if e != nil || !stop {
		t.Fatal(stop, e)
	}
	finishFixture(t, s, a, "SUCCEEDED")
	run, e := s.Run(ctx, p.RunID)
	if e != nil || run.State != "CANCELLED" {
		t.Fatal(run, e)
	}
	for _, task := range run.Tasks {
		if task.State != "CANCELLED" {
			t.Fatal(task.State)
		}
	}
	var n int
	if e = s.DB.QueryRow(`SELECT count(*) FROM outbox_events`).Scan(&n); e != nil || n != 2 {
		t.Fatal("cancel unlocked child", n, e)
	}
}
func TestOpsPostgresPausedClaimsAndIdempotentJournal(t *testing.T) {
	s := integrationStore(t)
	p := integrationPlan(t, s)
	ctx := context.Background()
	if _, e := s.Submit(ctx, p, NewID(), "test"); e != nil {
		t.Fatal(e)
	}
	if e := s.Action(ctx, p.RunID, "pause", "test"); e != nil {
		t.Fatal(e)
	}
	c, e := s.Claim(ctx, p.Tasks[0].ID, "worker-qc", 1)
	if e != nil || c.Decision != "paused" {
		t.Fatal(c, e)
	}
	if e = s.Action(ctx, p.RunID, "resume", "test"); e != nil {
		t.Fatal(e)
	}
	c = claimed(t, s, p.Tasks[0].ID, "qc", 1)
	pulse := Pulse{TaskID: c.TaskID, AttemptID: c.AttemptID, Token: c.Token, Stage: "COMPUTE", Lines: []LogLine{{Sequence: 1, Level: "error", Event: "numerical", Message: "test error"}}}
	for i := 0; i < 2; i++ {
		if _, e = s.Heartbeat(ctx, pulse); e != nil {
			t.Fatal(e)
		}
	}
	var n int
	if e = s.DB.QueryRow(`SELECT count(*) FROM ops_events WHERE attempt_id=$1 AND sequence=1`, c.AttemptID).Scan(&n); e != nil || n != 1 {
		t.Fatal(n, e)
	}
}

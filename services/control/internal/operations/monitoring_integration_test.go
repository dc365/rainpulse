//go:build integration

package operations

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

func TestOpsPostgresV2QueueHistoryAndWake(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	p := integrationPlan(t, s)
	if _, e := s.Submit(ctx, p, NewID(), "test"); e != nil {
		t.Fatal(e)
	}
	initial, e := s.Task(ctx, p.Tasks[0].ID)
	if e != nil || initial.QueuedAt == nil {
		t.Fatal(initial, e)
	}
	if e = s.Wake(ctx, p.RunID); e != nil {
		t.Fatal(e)
	}
	woken, _ := s.Task(ctx, initial.ID)
	if !woken.QueuedAt.Equal(*initial.QueuedAt) {
		t.Fatal("wake reset enqueue time")
	}
	claim := claimed(t, s, initial.ID, "qc", 1)
	first, _ := s.Task(ctx, initial.ID)
	if first.Attempts[0].QueuedAt == nil || !first.Attempts[0].QueuedAt.Equal(*initial.QueuedAt) {
		t.Fatal("claim lost enqueue history")
	}
	finishFixture(t, s, claim, "FAILED")
	other := claimed(t, s, p.Tasks[2].ID, "qc", 1)
	finishFixture(t, s, other, "SUCCEEDED")
	if e = s.Action(ctx, p.RunID, "retry_failed", "test"); e != nil {
		t.Fatal(e)
	}
	retried, _ := s.Task(ctx, initial.ID)
	if retried.QueuedAt == nil || !retried.QueuedAt.After(*initial.QueuedAt) {
		t.Fatal("retry did not start new wait")
	}
	if !retried.Attempts[0].QueuedAt.Equal(*initial.QueuedAt) {
		t.Fatal("overwrote old attempt timing")
	}
	if e = s.Wake(ctx, p.RunID); e != nil {
		t.Fatal(e)
	}
	// Existing legacy rows are intentionally not assigned a fake enqueue time.
	if _, e = s.DB.Exec(`UPDATE ops_tasks SET queued_at=NULL WHERE id=$1`, initial.ID); e != nil {
		t.Fatal(e)
	}
	if e = s.Wake(ctx, p.RunID); e != nil {
		t.Fatal(e)
	}
	unknown, _ := s.Task(ctx, initial.ID)
	if unknown.QueuedAt != nil {
		t.Fatal("wake invented historical timestamp")
	}
}
func TestOpsPostgresV2PoolControlsSurviveRegistration(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	p := integrationPlan(t, s)
	if _, e := s.Submit(ctx, p, NewID(), "test"); e != nil {
		t.Fatal(e)
	}
	a := claimed(t, s, p.Tasks[0].ID, "qc", 1)
	// A second root cannot execute concurrently on the same registered process.
	if c, e := s.Claim(ctx, p.Tasks[2].ID, "worker-qc", 1); e != nil || c.Decision != "busy" {
		t.Fatal(c, e)
	}
	if e := s.PoolAction(ctx, "qc", PoolAction{"drain", 1, "maintenance"}, "test"); e != nil {
		t.Fatal(e)
	}
	if e := s.Register(ctx, WorkerInfo{ID: "worker-qc", Identity: p.Tasks[0].Identity, Ready: true}); e != nil {
		t.Fatal(e)
	}
	if c, e := s.Claim(ctx, p.Tasks[2].ID, "worker-qc", 1); e != nil || c.Decision != "paused" {
		t.Fatal(c, e)
	}
	// Pause does not cancel a running task or suppress its receipts.
	stop, e := s.Heartbeat(ctx, Pulse{TaskID: a.TaskID, AttemptID: a.AttemptID, Token: a.Token, Stage: "COMPUTE"})
	if e != nil || stop {
		t.Fatal(stop, e)
	}
	finishFixture(t, s, a, "SUCCEEDED")
	pools, e := s.Pools(ctx)
	if e != nil {
		t.Fatal(e)
	}
	for _, p := range pools {
		if p.Kind == "qc" && (p.Active != 0 || p.Mode != "DRAINING") {
			t.Fatal(p)
		}
	}
	if e = s.PoolAction(ctx, "qc", PoolAction{"resume", 1, "stale"}, "test"); e == nil {
		t.Fatal("stale browser accepted")
	}
	if e = s.PoolAction(ctx, "qc", PoolAction{"resume", 2, "ready"}, "test"); e != nil {
		t.Fatal(e)
	}
	claimed(t, s, p.Tasks[2].ID, "qc", 1)
}
func TestOpsPostgresV2ServerLogFilteringBeyondFirstPage(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	p := integrationPlan(t, s)
	if _, e := s.Submit(ctx, p, NewID(), "test"); e != nil {
		t.Fatal(e)
	}
	// A match before 500 unrelated messages must remain searchable.
	if _, e := s.DB.Exec(`INSERT INTO ops_events(run_id,level,event,message) VALUES($1,'error','old.failure','checksum mismatch')`, p.RunID); e != nil {
		t.Fatal(e)
	}
	if _, e := s.DB.Exec(`INSERT INTO ops_events(run_id,level,event,message) SELECT $1,'info','noise','progress' FROM generate_series(1,500)`, p.RunID); e != nil {
		t.Fatal(e)
	}
	rows, e := s.QueryEvents(ctx, EventQuery{Run: p.RunID, Level: "error", Search: "checksum", Direction: "backward", Limit: 10})
	if e != nil || len(rows) != 1 {
		t.Fatal(rows, e)
	}
	first, e := s.QueryEvents(ctx, EventQuery{Run: p.RunID, Direction: "backward", Limit: 200})
	if e != nil || len(first) != 201 {
		t.Fatal(len(first), e)
	}
	older, e := s.QueryEvents(ctx, EventQuery{Run: p.RunID, Direction: "backward", Before: first[199].ID, Limit: 200})
	if e != nil || len(older) != 201 || older[0].ID >= first[199].ID {
		t.Fatal(len(older), e)
	}
}
func TestOpsPostgresV2DataInventoryAndSources(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	// Minimal projections use the actual production column names queried by v2.
	_, e := s.DB.Exec(`CREATE TABLE radars(radar_id text PRIMARY KEY);
 CREATE TABLE radar_scans(scan_id uuid PRIMARY KEY,radar_id text,volume_end_time timestamptz,received_at timestamptz);
 CREATE TABLE radar_scan_runs(run_id uuid PRIMARY KEY,scan_id uuid,radar_config_version text,status text,normalized_uri text,qc_uri text,grid_uri text,created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now());
 CREATE TABLE jobs(job_id uuid PRIMARY KEY,run_id uuid,job_type text,status text,config_version text,created_at timestamptz);
 CREATE TABLE analysis_cycles(analysis_id uuid PRIMARY KEY,analysis_time timestamptz,grid_id text,status text,mosaic_uri text,analysis_uri text,config_version text);
 CREATE TABLE analysis_cycle_radars(analysis_id uuid,scan_id uuid,state text);
 INSERT INTO radars VALUES('z9591'),('z9598');`)
	if e != nil {
		t.Fatal(e)
	}
	at := time.Date(2026, 9, 22, 1, 0, 0, 0, time.UTC)
	ids := []string{}
	for i := 0; i < 151; i++ {
		id, run := NewID(), NewID()
		ids = append(ids, id)
		if _, e = s.DB.Exec(`INSERT INTO radar_scans VALUES($1,'z9591',$2,$2);`, id, at); e != nil {
			t.Fatal(e)
		}
		if _, e = s.DB.Exec(`INSERT INTO radar_scan_runs(run_id,scan_id,radar_config_version,status,normalized_uri) VALUES($1,$2,'site-v1','NORMALIZED','s3://r/volume')`, run, id); e != nil {
			t.Fatal(e)
		}
	}
	q := DataQuery{Start: at.Add(-time.Minute), End: at.Add(time.Minute), Stage: "qc_missing"}
	seen := map[string]bool{}
	var before time.Time
	cursor := ""
	for {
		items, e := s.DataScans(ctx, q, 50, before, cursor)
		if e != nil {
			t.Fatal(e)
		}
		more := len(items) > 50
		if more {
			items = items[:50]
		}
		for _, raw := range items {
			var r struct {
				ID       string    `json:"id"`
				Observed time.Time `json:"observed_at"`
			}
			if e = json.Unmarshal(raw, &r); e != nil {
				t.Fatal(e)
			}
			if seen[r.ID] {
				t.Fatal("duplicate")
			}
			seen[r.ID] = true
			before, cursor = r.Observed, r.ID
		}
		if !more {
			break
		}
	}
	if len(seen) != 151 {
		t.Fatal("lost identical-time page edges", len(seen))
	}
	summary, e := s.DataSummary(ctx, q)
	if e != nil || len(summary) != 2 {
		t.Fatal(summary, e)
	}
	var empty struct {
		Registered int `json:"registered"`
	}
	if e = json.Unmarshal(summary[1], &empty); e != nil || empty.Registered != 0 {
		t.Fatal(empty, e)
	}
	lineage, e := s.ScanLineage(ctx, ids[0])
	if e != nil || lineage.Automatic == nil || lineage.Checks == nil {
		t.Fatal(lineage, e)
	}
}

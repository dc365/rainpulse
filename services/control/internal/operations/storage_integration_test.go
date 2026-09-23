//go:build integration

package operations

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// Uses the real PostgreSQL row locks and the explicit schema v1/v2/v3 in a
// disposable schema. Never run against public/production tables.
func retentionStore(t *testing.T) *Store {
	s := integrationStore(t)
	_, e := s.DB.Exec(`CREATE TABLE jobs(request_payload jsonb);
 CREATE TABLE radar_scan_runs(normalized_uri text,qc_uri text,grid_uri text);
 CREATE TABLE analysis_cycles(mosaic_uri text,analysis_uri text);
 CREATE TABLE input_assets(object_uri text);
 CREATE TABLE product_assets(object_uri text);`)
	if e != nil {
		t.Fatal(e)
	}
	t.Setenv("RAINPULSE_OBJECT_STORE_ENDPOINT", "http://minio:9000")
	return s
}
func retentionRun(t *testing.T, s *Store, scan, fp string, age time.Duration) string {
	now := time.Now().UTC()
	run, task, attempt := NewID(), NewID(), NewID()
	i := identity("qc")
	i.Fingerprint = fp
	p := Plan{ID: NewID(), RunID: run, Selection: Selection{Name: "retention-test"}, Tasks: []Spec{{ID: task, Kind: "qc", Name: "qc", Identity: i, Request: JSON(map[string]any{"payload": map[string]any{"scan_id": scan}})}}}
	if e := p.Seal(now); e != nil {
		t.Fatal(e)
	}
	if e := s.SavePlan(context.Background(), p); e != nil {
		t.Fatal(e)
	}
	if _, e := s.Submit(context.Background(), p, NewID(), "test"); e != nil {
		t.Fatal(e)
	}
	prefix, _ := CandidateAttemptPrefix(run, task, attempt)
	req := JSON(map[string]any{"run_id": run, "job_id": task, "event_id": attempt, "payload": map[string]string{"output_prefix": prefix}})
	result := Candidate{Asset: AssetRef{URI: prefix + "qc.zarr", MarkerSHA256: strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), SizeBytes: 123}, FinishedAt: now.Add(-age), StartedAt: now.Add(-age - time.Minute), CandidateOnly: true}
	_, e := s.DB.Exec(`INSERT INTO ops_attempts(id,task_id,number,worker_id,token_sha256,state,stage,request,started_at,heartbeat_at,lease_until,finished_at,result) VALUES($1,$2,1,'worker','token','SUCCEEDED','COMMIT',$3,$4,$4,$4,$4,$5)`, attempt, task, string(req), now.Add(-age), string(JSON(result)))
	if e != nil {
		t.Fatal(e)
	}
	_, e = s.DB.Exec(`UPDATE ops_tasks SET state='SUCCEEDED',result=$2,current_attempt=$3,attempt_no=1,updated_at=$4 WHERE id=$1`, task, string(JSON(result)), attempt, now.Add(-age))
	if e != nil {
		t.Fatal(e)
	}
	_, e = s.DB.Exec(`UPDATE ops_runs SET state='SUCCEEDED',updated_at=$2 WHERE id=$1`, run, now.Add(-age))
	if e != nil {
		t.Fatal(e)
	}
	return run
}
func TestOpsPostgresRetentionProtectsLatestDefaultPinsAndReferences(t *testing.T) {
	s := retentionStore(t)
	ctx := context.Background()
	scan := NewID()
	fp := Digest([]byte("current"))
	oldfp := Digest([]byte("old"))
	old := retentionRun(t, s, scan, oldfp, 96*time.Hour)
	current := retentionRun(t, s, scan, fp, 72*time.Hour)
	latest := retentionRun(t, s, scan, Digest([]byte("experiment")), 48*time.Hour)
	if _, e := s.DB.Exec(`UPDATE ops_release_channels SET current_fingerprint=$1 WHERE kind='qc'`, fp); e != nil {
		t.Fatal(e)
	}
	p, e := s.PreviewRetention(ctx, RetentionPolicy{1, 24, 5})
	if e != nil {
		t.Fatal(e)
	}
	if len(p.Targets) != 1 || p.Targets[0].RunID != old {
		t.Fatalf("current %s/latest %s must be retained: %+v", current, latest, p)
	}
	if e = s.PinStorage(ctx, old, "keep evidence", true); e != nil {
		t.Fatal(e)
	}
	p, e = s.PreviewRetention(ctx, RetentionPolicy{1, 24, 5})
	if e != nil || len(p.Targets) != 0 {
		t.Fatal(p, e)
	}
	if e = s.PinStorage(ctx, old, "release pin", false); e != nil {
		t.Fatal(e)
	}
	if _, e = s.DB.Exec(`INSERT INTO product_assets(object_uri) VALUES($1)`, managedPrefix(old)+"protected.png"); e != nil {
		t.Fatal(e)
	}
	p, e = s.PreviewRetention(ctx, RetentionPolicy{1, 24, 5})
	if e != nil || len(p.Targets) != 0 {
		t.Fatal("formal reference lost", p, e)
	}
}
func TestOpsPostgresRetentionRechecksAndFencesNewAdmissions(t *testing.T) {
	s := retentionStore(t)
	ctx := context.Background()
	scan := NewID()
	fp := Digest([]byte("version"))
	old := retentionRun(t, s, scan, fp, 96*time.Hour)
	_ = retentionRun(t, s, scan, fp, 48*time.Hour)
	p, e := s.PreviewRetention(ctx, RetentionPolicy{1, 24, 5})
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.DB.Exec(`UPDATE ops_pool_controls SET mode='DRAINING'`); e != nil {
		t.Fatal(e)
	}
	if e = s.PinStorage(ctx, old, "late pin", true); e != nil {
		t.Fatal(e)
	}
	if _, e = s.BeginRetention(ctx, p.ID, p.Digest); e == nil {
		t.Fatal("late pin did not invalidate preview")
	}
	if e = s.PinStorage(ctx, old, "release pin", false); e != nil {
		t.Fatal(e)
	}
	p, e = s.PreviewRetention(ctx, RetentionPolicy{1, 24, 5})
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.BeginRetention(ctx, p.ID, p.Digest); e != nil {
		t.Fatal(e)
	}
	if _, e = s.Claim(ctx, NewID(), "worker", 1); e == nil {
		t.Fatal("maintenance admitted new claim")
	}
	rows := []PurgePrefixReceipt{}
	for _, a := range p.Targets[0].Attempts {
		rows = append(rows, PurgePrefixReceipt{Prefix: a.Prefix, Empty: false, Error: "interrupted"})
	}
	if e = s.FinishRetention(ctx, p.ID, PurgeReceipt{Digest: p.Digest, Prefixes: rows}); e != nil {
		t.Fatal(e)
	}
	if e = s.Action(ctx, old, "retry_failed", "test"); e == nil {
		t.Fatal("retired run accepted retry")
	}
	// An input that points at the retired run cannot be frozen into a new plan.
	p2 := Plan{ID: NewID(), RunID: NewID(), Selection: Selection{Name: "blocked-input"}, Tasks: []Spec{{ID: NewID(), Kind: "qc", Identity: identity("qc"), InputURIs: []string{managedPrefix(old) + "qc.zarr"}, Request: json.RawMessage(`{"payload":{}}`)}}}
	if e = p2.Seal(time.Now().UTC()); e != nil {
		t.Fatal(e)
	}
	if e = s.SavePlan(ctx, p2); e == nil {
		t.Fatal("retired input accepted")
	}
	// Continue the same frozen plan, even though it is now ERROR, never reselect.
	if _, e = s.BeginRetention(ctx, p.ID, p.Digest); e != nil {
		t.Fatal(e)
	}
	for i := range rows {
		rows[i].Empty = true
		rows[i].Error = ""
	}
	if e = s.FinishRetention(ctx, p.ID, PurgeReceipt{Digest: p.Digest, Prefixes: rows}); e != nil {
		t.Fatal(e)
	}
	state, e := s.RunStorageState(ctx, old)
	if e != nil || state != "DELETED" {
		t.Fatal(state, e)
	}
}

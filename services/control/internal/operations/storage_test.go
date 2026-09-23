package operations

import (
	"context"
	"database/sql/driver"
	"errors"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func storageOpenStep() sqlStep {
	return sqlStep{Contains: "FROM ops_storage_control WHERE id=1 FOR SHARE", Columns: []string{"session", "report", "threshold", "min", "age"}, Rows: [][]driver.Value{{nil, "", int64(95), int64(1 << 30), int64(180)}}}
}
func fixtureRetention() RetentionPlan {
	run, task, attempt := NewID(), NewID(), NewID()
	prefix, _ := CandidateAttemptPrefix(run, task, attempt)
	now := time.Now().UTC()
	p := RetentionPlan{ID: NewID(), Policy: RetentionPolicy{1, 24, 5}, Targets: []RetentionTarget{{RunID: run, Name: "旧候选", Attempts: []RetentionAttempt{{ID: attempt, TaskID: task, Kind: "qc", Prefix: prefix}}}}, CreatedAt: now, ExpiresAt: now.Add(time.Minute), Scope: "managed_candidates_only", ObjectStoreEndpoint: "http://minio:9000"}
	p.Digest = CanonicalDigest(p)
	return p
}
func TestStorageReportCountersAndPressure(t *testing.T) {
	now := time.Now().UTC()
	pct := 96.0
	r := StorageReport{ID: "host-data", Host: "node", Label: "data", Path: "/data", SampledAt: now, TotalBytes: 10 << 30, AvailableBytes: 2 << 30, InodesTotal: 1000, InodesFree: 40, InodeUsedPercent: &pct}
	if e := r.Validate(now); e != nil {
		t.Fatal(e)
	}
	if PressureProblem(r, now, 3*time.Minute, 95, 1<<30) == "" {
		t.Fatal("inode full not gated")
	}
	pct = 1
	if r.Validate(now) == nil {
		t.Fatal("lying client percentage accepted")
	}
	pct = 96
	r.SampledAt = now.Add(-time.Hour)
	if PressureProblem(r, now, 3*time.Minute, 99, 1) == "" {
		t.Fatal("stale not gated")
	}
	r.SampledAt = now
	r.InodesTotal = 0
	r.InodesFree = 0
	r.InodeUsedPercent = nil
	if r.Validate(now) != nil {
		t.Fatal("unsupported counters must remain unknown")
	}
	if PressureProblem(r, now, time.Minute, 95, 1) == "" {
		t.Fatal("unknown inode treated healthy")
	}
	pct = math.NaN()
	r.InodesTotal = 100
	r.InodeUsedPercent = &pct
	if r.Validate(now) == nil {
		t.Fatal("NaN accepted")
	}
}
func TestRetentionPolicyAndFrozenTargets(t *testing.T) {
	for _, p := range []RetentionPolicy{{0, 24, 1}, {1, 0, 1}, {1, 24, 21}, {6, 24, 1}} {
		if p.Validate() == nil {
			t.Fatal(p)
		}
	}
	p := fixtureRetention()
	if e := p.Validate(time.Now()); e != nil {
		t.Fatal(e)
	}
	p.Targets[0].Attempts[0].Prefix = "s3://rainpulse/radar/"
	p.Digest = ""
	p.Digest = CanonicalDigest(p)
	if p.Validate(time.Now()) == nil {
		t.Fatal("raw-data prefix accepted")
	}
}
func TestRetentionPlanTamperAndExpiry(t *testing.T) {
	p := fixtureRetention()
	p.Targets[0].Name = "changed"
	if p.Validate(time.Now()) == nil {
		t.Fatal("tamper accepted")
	}
	p = fixtureRetention()
	if p.Validate(p.ExpiresAt) == nil {
		t.Fatal("expired accepted")
	}
}
func TestCandidateReferenceDetection(t *testing.T) {
	run := NewID()
	for _, uri := range []string{managedPrefix(run) + "a", managedPrefix(run)} {
		got, ok := CandidateRunFromURI(uri)
		if !ok || got != run {
			t.Fatal(uri)
		}
	}
	for _, uri := range []string{"s3://rainpulse/radar/" + run, "s3://other/operations/" + run, "https://host/operations/" + run} {
		if _, ok := CandidateRunFromURI(uri); ok {
			t.Fatal(uri)
		}
	}
}
func TestCleanupReceiptExactCoverage(t *testing.T) {
	p := fixtureRetention()
	prefix := p.Targets[0].Attempts[0].Prefix
	r := PurgeReceipt{Digest: p.Digest, Prefixes: []PurgePrefixReceipt{{Prefix: prefix, DeletedVersions: 20, Empty: true}}}
	if e := r.Validate(p); e != nil {
		t.Fatal(e)
	}
	r.Prefixes = append(r.Prefixes, r.Prefixes[0])
	if r.Validate(p) == nil {
		t.Fatal("duplicate receipt")
	}
	r.Prefixes = nil
	if r.Validate(p) == nil {
		t.Fatal("missing receipt")
	}
}
func TestStorageMaintenanceBlocksClaimBeforeAllocation(t *testing.T) {
	step := storageOpenStep()
	step.Rows[0][0] = NewID()
	s, d := scriptedStore(t, step)
	_, e := s.Claim(context.Background(), NewID(), "w", 1)
	if status, _, _ := errorStatus(e); status != 409 || d.commits != 0 {
		t.Fatal(e)
	}
}
func TestStoragePressureBlocksClaimButNotHeartbeatContract(t *testing.T) {
	step := storageOpenStep()
	step.Rows[0][1] = "missing-host"
	s, d := scriptedStore(t, step, sqlStep{Contains: "FROM ops_storage_reports", Err: errors.New("missing")})
	_, e := s.Claim(context.Background(), NewID(), "w", 1)
	if status, _, _ := errorStatus(e); status != 409 || d.commits != 0 {
		t.Fatal(e)
	}
}
func TestRetiredRunCannotBeRetried(t *testing.T) {
	s, d := scriptedStore(t, storageOpenStep(), sqlStep{Contains: "FROM ops_retired_runs", Columns: []string{"retired"}, Rows: [][]driver.Value{{true}}})
	e := s.Action(context.Background(), NewID(), "retry_failed", "admin")
	if status, _, _ := errorStatus(e); status != 410 || d.commits != 0 {
		t.Fatal(e)
	}
}
func TestDefaultReleaseDoesNotRewriteFrozenIdentity(t *testing.T) {
	old := identity("qc")
	channels := []ReleaseChannel{{Kind: "qc", Current: Digest([]byte("new"))}}
	chosen := PreferredIdentity(old, channels)
	if chosen.Fingerprint == old.Fingerprint || chosen.CodeSHA256 != old.CodeSHA256 {
		t.Fatal(chosen)
	}
	// Existing Recheck receives old, not PreferredIdentity: original task unchanged.
	if old.Fingerprint != identity("qc").Fingerprint {
		t.Fatal("identity mutated")
	}
}
func TestBeginCleanupRejectsLiveOrUndrainedWorkers(t *testing.T) {
	p := fixtureRetention()
	s, d := scriptedStore(t,
		sqlStep{Contains: "FROM ops_storage_control WHERE id=1 FOR UPDATE", Columns: []string{"session"}, Rows: [][]driver.Value{{nil}}},
		sqlStep{Contains: "FROM ops_retention_plans", Columns: []string{"document", "state"}, Rows: [][]driver.Value{{JSON(p), "PREVIEW"}}},
		sqlStep{Contains: "FROM ops_attempts", Columns: []string{"working"}, Rows: [][]driver.Value{{true}}})
	_, e := s.BeginRetention(context.Background(), p.ID, p.Digest)
	if status, _, _ := errorStatus(e); status != 409 || d.commits != 0 {
		t.Fatal(e)
	}
}
func TestCleanupResumeKeepsOriginalTargets(t *testing.T) {
	p := fixtureRetention()
	p.CreatedAt = time.Now().Add(-2 * time.Hour)
	p.ExpiresAt = time.Now().Add(-time.Hour)
	p.Digest = ""
	p.Digest = CanonicalDigest(p)
	s, d := scriptedStore(t,
		sqlStep{Contains: "FROM ops_storage_control WHERE id=1 FOR UPDATE", Columns: []string{"session"}, Rows: [][]driver.Value{{p.ID}}},
		sqlStep{Contains: "FROM ops_retention_plans", Columns: []string{"document", "state"}, Rows: [][]driver.Value{{JSON(p), "DELETING"}}},
		sqlStep{Contains: "FROM ops_attempts", Columns: []string{"working"}, Rows: [][]driver.Value{{false}}},
		sqlStep{Contains: "UPDATE ops_storage_control", Exec: true}, sqlStep{Contains: "UPDATE ops_retention_plans", Exec: true}, sqlStep{Contains: "INSERT INTO ops_storage_events", Exec: true})
	got, e := s.BeginRetention(context.Background(), p.ID, p.Digest)
	if e != nil || got.Digest != p.Digest || d.commits != 1 {
		t.Fatal(got, e)
	}
}
func TestStorageHandlersRequireAdminCredential(t *testing.T) {
	for _, path := range []string{"/storage", "/releases", "/storage/cleanup/plans", "/storage/report"} {
		h := NewHandler(&Service{}, http.NotFoundHandler(), HTTPOptions{AdminToken: "secret"})
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", "/api/v1/admin/ops"+path, nil))
		if w.Code != 401 {
			t.Fatal(path, w.Code)
		}
	}
}
func TestStorageMalformedRequestsNeverReachDB(t *testing.T) {
	for path, body := range map[string]string{"/storage/cleanup/plans": `{"keep_latest":0,"minimum_age_hours":24,"limit":1}`, "/releases/qc/select": `{"fingerprint":"latest","expected_revision":1,"reason":"x"}`} {
		h := NewHandler(&Service{Store: &Store{}}, http.NotFoundHandler(), HTTPOptions{AdminToken: "secret"})
		w := httptest.NewRecorder()
		r := httptest.NewRequest("POST", "/api/v1/admin/ops"+path, strings.NewReader(body))
		r.Header.Set("Authorization", "Bearer secret")
		h.ServeHTTP(w, r)
		if w.Code != 422 {
			t.Fatal(path, w.Code, w.Body.String())
		}
	}
}

func TestCleanupResumptionRevalidatesIntegrity(t *testing.T) {
	p := fixtureRetention()
	p.ObjectStoreEndpoint = "http://minio:9000/different"
	p.Digest = ""
	p.Digest = CanonicalDigest(p)
	if p.ValidateIntegrity() == nil {
		t.Fatal("invalid endpoint accepted")
	}
	p = fixtureRetention()
	p.Targets[0].Attempts = append(p.Targets[0].Attempts, p.Targets[0].Attempts[0])
	p.Digest = ""
	p.Digest = CanonicalDigest(p)
	if p.ValidateIntegrity() == nil {
		t.Fatal("duplicate attempt accepted")
	}
}

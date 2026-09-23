package operations

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"testing"
	"time"
)

func windowValues() url.Values {
	return url.Values{"start": {"2026-09-22T00:00:00+08:00"}, "end": {"2026-09-22T06:00:00+08:00"}}
}
func TestDataWindowAndFilter(t *testing.T) {
	q := windowValues()
	q.Set("radar", " Z9591 ")
	d, e := ParseDataQuery(q)
	if e != nil || d.Radar != "z9591" || d.Stage != "all" || d.Start.Hour() != 16 {
		t.Fatal(d, e)
	}
	cursor := encodeCursor(d.Start, NewID(), d.filter())
	if _, _, e = decodeCursor(cursor, d.filter()); e != nil {
		t.Fatal(e)
	}
	d.Stage = "qc_missing"
	if _, _, e = decodeCursor(cursor, d.filter()); e == nil {
		t.Fatal("cursor accepted different stage")
	}
}
func TestDataRejectsUnboundedOrInvalidQueries(t *testing.T) {
	for name, values := range map[string]url.Values{
		"missing": {}, "naive": {"start": {"2026-09-22T00:00:00"}, "end": {"2026-09-22T06:00:00Z"}},
		"reverse": {"start": {"2026-09-22T06:00:00Z"}, "end": {"2026-09-22T00:00:00Z"}},
		"wide":    {"start": {"2026-09-20T00:00:00Z"}, "end": {"2026-09-22T00:00:00Z"}},
	} {
		t.Run(name, func(t *testing.T) {
			if _, e := ParseDataQuery(values); e == nil {
				t.Fatal("accepted")
			}
		})
	}
	for _, field := range []string{"stage", "radar"} {
		q := windowValues()
		q.Set(field, "../invalid")
		if _, e := ParseDataQuery(q); e == nil {
			t.Fatal(field)
		}
	}
}
func TestDataAssetLookupDoesNotGuess(t *testing.T) {
	raw := json.RawMessage(`{"normalized_uri":"s3://r/normalized","qc_uri":null,"grid_uri":""}`)
	if uri, e := scanAssetURI(raw, "normalized"); e != nil || uri != "s3://r/normalized" {
		t.Fatal(uri, e)
	}
	for _, stage := range []string{"qc", "grid", "raw", "../../private"} {
		if _, e := scanAssetURI(raw, stage); e == nil {
			t.Fatal(stage)
		}
	}
}
func TestDistributionKeepsZeroRejectsUnknown(t *testing.T) {
	values := []float64{0, 4, 2, math.NaN(), math.Inf(1), -1}
	d := Distribution(values)
	if d.Samples != 3 || *d.P50 != 2 || *d.P95 != 4 || *d.Maximum != 4 {
		t.Fatal(d)
	}
	if values[1] != 4 {
		t.Fatal("mutated input")
	}
	empty := Distribution(nil)
	if empty.Samples != 0 || empty.P50 != nil || empty.P95 != nil {
		t.Fatal(empty)
	}
}
func TestPerformanceSeparatesVersionsAndFailures(t *testing.T) {
	start := time.Date(2026, 9, 22, 1, 0, 0, 0, time.UTC)
	end := start.Add(time.Second)
	queued := start.Add(-2 * time.Second)
	all := []PerformanceSample{
		{ID: "a", Kind: "qc", Fingerprint: "v1", State: "SUCCEEDED", StartedAt: start, FinishedAt: &end, QueuedAt: &queued, Metrics: map[string]float64{"qc_core_ms": 4}, WorkerID: "w1"},
		{ID: "b", Kind: "qc", Fingerprint: "v1", State: "FAILED", StartedAt: start, FinishedAt: &end, Metrics: map[string]float64{"qc_core_ms": 999}},
		{ID: "c", Kind: "qc", Fingerprint: "v2", State: "SUCCEEDED", StartedAt: start, FinishedAt: &end, Metrics: map[string]float64{}, WorkerID: "w2"},
	}
	r := BuildPerformance(PerformanceQuery{}, all, start)
	if r.Attempts != 3 || len(r.Groups) != 2 || len(r.Slow) != 2 {
		t.Fatal(r)
	}
	g := r.Groups[0]
	if g.Attempts != 2 || g.States["FAILED"] != 1 || *g.Metrics["qc_core_ms"].P95 != 4 || *g.Metrics["queue_ms"].P50 != 2000 {
		t.Fatal(g)
	}
	if r.Groups[1].Metrics["queue_ms"].Samples != 0 || r.Groups[1].Metrics["qc_core_ms"].P50 != nil {
		t.Fatal("unknown timings inferred")
	}
	if all[0].Metrics["queue_ms"] != 0 {
		t.Fatal("changed caller metrics")
	}
}
func TestPerformanceDoesNotAverageWorkerQuantiles(t *testing.T) {
	samples := make([]PerformanceSample, 100)
	for i := range samples {
		samples[i] = PerformanceSample{Kind: "qc", Fingerprint: "same", State: "SUCCEEDED", WorkerID: "many", Metrics: map[string]float64{"qc_core_ms": 0}}
	}
	samples[0].WorkerID = "rare"
	samples[0].Metrics["qc_core_ms"] = 100
	r := BuildPerformance(PerformanceQuery{}, samples, time.Now())
	if *r.Groups[0].Metrics["qc_core_ms"].P95 != 0 {
		t.Fatal("averaged per-worker quantiles")
	}
}
func TestPerformanceDBTimesOverrideUntrustedMetricNames(t *testing.T) {
	a := PerformanceSample{StartedAt: time.Now(), Metrics: map[string]float64{"queue_ms": 123, "attempt_elapsed_ms": 345}}
	m := measuredTiming(a)
	if _, ok := m["queue_ms"]; ok {
		t.Fatal(m)
	}
	if _, ok := m["attempt_elapsed_ms"]; ok {
		t.Fatal(m)
	}
	future := a.StartedAt.Add(time.Minute)
	a.QueuedAt = &future
	if _, ok := measuredTiming(a)["queue_ms"]; ok {
		t.Fatal("negative queue became zero")
	}
}
func TestPerformanceQueryBounds(t *testing.T) {
	q := windowValues()
	if _, e := ParsePerformanceQuery(q); e != nil {
		t.Fatal(e)
	}
	q.Set("kind", "raw")
	if _, e := ParsePerformanceQuery(q); e == nil {
		t.Fatal("invalid kind")
	}
	q.Del("kind")
	q.Set("fingerprint", "short")
	if _, e := ParsePerformanceQuery(q); e == nil {
		t.Fatal("short identity")
	}
	q.Del("fingerprint")
	q.Set("end", "2026-10-01T00:00:00Z")
	if _, e := ParsePerformanceQuery(q); e == nil {
		t.Fatal("wide")
	}
}
func TestPoolActionBounds(t *testing.T) {
	for _, a := range []PoolAction{{"drain", 1, "维护"}, {"resume", 2, "恢复"}} {
		if e := a.Validate(); e != nil {
			t.Fatal(e)
		}
	}
	for _, a := range []PoolAction{{"stop", 1, "maintenance"}, {"drain", 0, "maintenance"}, {"resume", 1, "  "}, {"drain", 1, strings.Repeat("字", 257)}} {
		if e := a.Validate(); e == nil {
			t.Fatal(a)
		}
	}
}
func TestMatchWorkerSkipsPausedPools(t *testing.T) {
	i := identity("qc")
	now := time.Now()
	workers := []WorkerInfo{{ID: "worker", Identity: i, Ready: true, SeenAt: now, PoolMode: "DRAINING"}}
	if _, e := MatchWorker(i, workers, now); e == nil {
		t.Fatal("matched draining worker")
	}
	workers[0].PoolMode = "ACCEPTING"
	if _, e := MatchWorker(i, workers, now); e != nil {
		t.Fatal(e)
	}
}
func TestJournalFilterAndBoundedPaging(t *testing.T) {
	id := NewID()
	q := url.Values{"direction": {"backward"}, "before": {"500"}, "level": {"error"}, "attempt": {id}, "q": {"checksum"}, "limit": {"50"}}
	f, e := ParseEventQuery(q, id, "")
	if e != nil || f.Before != 500 || f.Level != "error" || f.Search != "checksum" {
		t.Fatal(f, e)
	}
	for name, value := range map[string]string{"after": "1", "direction": "invalid", "limit": "501", "level": "fatal", "attempt": "none"} {
		other := url.Values{}
		for k, v := range q {
			other[k] = v
		}
		other.Set(name, value)
		if _, e = ParseEventQuery(other, id, ""); e == nil {
			t.Fatal(name)
		}
	}
	if _, e = ParseEventQuery(nil, "", ""); e == nil {
		t.Fatal("global logs allowed")
	}
}
func TestNewAdminEndpointsRemainAuthenticated(t *testing.T) {
	h := NewHandler(&Service{}, http.NotFoundHandler(), HTTPOptions{AdminToken: "secret"})
	for _, path := range []string{"/data/scans", "/data/summary", "/performance", "/pools", "/pools/events"} {
		r := httptest.NewRequest("GET", "/api/v1/admin/ops"+path, nil)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 401 {
			t.Fatal(path, w.Code)
		}
	}
}
func TestNewQueriesRejectBeforeDatabaseAccess(t *testing.T) {
	h := NewHandler(&Service{Store: &Store{}}, http.NotFoundHandler(), HTTPOptions{AdminToken: "secret"})
	for _, path := range []string{"/data/scans", "/data/summary", "/performance", "/tasks/" + NewID() + "/events?direction=no"} {
		r := httptest.NewRequest("GET", "/api/v1/admin/ops"+path, nil)
		r.Header.Set("Authorization", "Bearer secret")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 422 {
			t.Fatal(path, w.Code, w.Body.String())
		}
	}
}
func TestPoolMutationsHonorReleaseAdmission(t *testing.T) {
	called := false
	h := NewHandler(&Service{}, http.NotFoundHandler(), HTTPOptions{AdminToken: "secret", Admit: func(context.Context) (func(), error) { called = true; return nil, Conflict("paused") }})
	r := httptest.NewRequest("POST", "/api/v1/admin/ops/pools/qc/action", strings.NewReader(`{"action":"drain","reason":"maintenance","expected_revision":1}`))
	r.Header.Set("Authorization", "Bearer secret")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if !called || w.Code != 503 {
		t.Fatal(called, w.Code)
	}
}
func TestPerformanceMetricKeysStable(t *testing.T) {
	r := BuildPerformance(PerformanceQuery{}, []PerformanceSample{{Kind: "render", Fingerprint: "f", State: "FAILED"}}, time.Now())
	got := r.Groups[0].Metrics
	for _, k := range performanceMetricKeys {
		if !reflect.DeepEqual(got[k], MetricDistribution{}) {
			t.Fatal(k, got[k])
		}
	}
}

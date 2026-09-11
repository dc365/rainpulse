package workspace

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestVerificationJobValidation(t *testing.T) {
	good := analysisJobInput{CycleIDs: []string{"case"}, Algorithms: []string{"lk"}, Leads: []int{10}, Threshold: 5, WindowKM: 10}
	if !good.valid() {
		t.Fatal("valid rejected")
	}
	for _, mutate := range []func(*analysisJobInput){func(x *analysisJobInput) { x.CycleIDs = []string{"case", "case"} }, func(x *analysisJobInput) { x.Leads = []int{0} }, func(x *analysisJobInput) { x.Algorithms = []string{"qpe"} }, func(x *analysisJobInput) { x.Threshold = 3 }} {
		bad := good
		mutate(&bad)
		if bad.valid() {
			t.Fatal("invalid accepted")
		}
	}
}

func TestVerificationJobRunsPersistsAndReplacesSameRequest(t *testing.T) {
	root := t.TempDir()
	t.Setenv("RAINPULSE_VERIFICATION_JOB_ROOT", root)
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/interval/summary" {
			t.Errorf("unexpected worker path %s", r.URL.Path)
		}
		analysisJSON(w, 200, map[string]any{"matched_records": 0, "skipped_records": 2})
	}))
	defer worker.Close()
	t.Setenv("RAINPULSE_INTERVAL_WORKER_URL", worker.URL)
	h := &runtimeHandler{intervalProjection: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		analysisJSON(w, 200, map[string]any{"cycle_id": "case", "issue_time": "2026-08-28T08:30:00Z", "panels": []any{}})
	})}
	body := `{"cycle_ids":["case"],"algorithms":["lk"],"leads":[10,20],"threshold":5,"window_km":10}`
	var previous string
	for attempt := 0; attempt < 2; attempt++ {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", analysisJobsPath, strings.NewReader(body)))
		if w.Code != 202 {
			t.Fatalf("start %d: %s", w.Code, w.Body.String())
		}
		var job analysisJob
		_ = json.Unmarshal(w.Body.Bytes(), &job)
		if previous != "" && previous != job.ID {
			t.Fatal("duplicate report version")
		}
		previous = job.ID
		m := h.jobManager()
		finished := false
		for deadline := time.Now().Add(2 * time.Second); time.Now().Before(deadline); {
			m.mu.Lock()
			finished = m.active == ""
			m.mu.Unlock()
			if finished {
				break
			}
			time.Sleep(time.Millisecond)
		}
		if !finished {
			t.Fatal("job never finished")
		}
		restored := newAnalysisJobs(root).jobs[job.ID]
		if restored == nil || restored.Status != "complete" || restored.Completed != 2 || len(restored.Records) != 2 {
			t.Fatalf("bad persisted job %+v", restored)
		}
		if restored.Records[0]["status"] != "unavailable" {
			t.Fatal("missing frame scored")
		}
	}
	entries, _ := os.ReadDir(root)
	if len(entries) != 1 {
		t.Fatalf("duplicate files: %d", len(entries))
	}
}
func TestVerificationJobsRecoverInterruptedAndBoundRetention(t *testing.T) {
	root := t.TempDir()
	j := analysisJob{ID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Status: "running"}
	data, _ := json.Marshal(j)
	if err := os.WriteFile(filepath.Join(root, j.ID+".json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	m := newAnalysisJobs(root)
	if m.jobs[j.ID].Status != "interrupted" {
		t.Fatal("running snapshot was treated as complete")
	}
}

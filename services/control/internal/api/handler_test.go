package api_test

import (
	"bufio"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/api"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

func TestSystemStatusReportsReadyControlPlane(t *testing.T) {
	handler := api.NewHandler(api.Options{Version: "test-version"})
	request := httptest.NewRequest(http.MethodGet, "/api/v1/system/status", nil)
	response := httptest.NewRecorder()

	handler.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", response.Code)
	}
	if got := response.Header().Get("Content-Type"); got != "application/json" {
		t.Fatalf("expected application/json content type, got %q", got)
	}

	var body struct {
		Service string `json:"service"`
		Status  string `json:"status"`
		Version string `json:"version"`
	}
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		t.Fatalf("decode status response: %v", err)
	}

	if body.Service != "rainpulse-control" {
		t.Fatalf("expected rainpulse-control service, got %q", body.Service)
	}
	if body.Status != "ready" {
		t.Fatalf("expected ready status, got %q", body.Status)
	}
	if body.Version != "test-version" {
		t.Fatalf("expected injected version, got %q", body.Version)
	}
}

func TestRunQueriesAndRerunUseControlPlane(t *testing.T) {
	now := time.Date(2026, 8, 24, 3, 0, 0, 0, time.UTC)
	run := workflow.Run{
		ID:            uuid.MustParse("f3641335-13a3-4f68-96c0-56a5e0e684d7"),
		IssueTime:     now,
		GridID:        "rp003-sim-grid",
		ConfigVersion: "rp003-sim-v1",
		Status:        workflow.RunBaselineRunning,
		CreatedAt:     now,
		UpdatedAt:     now,
	}
	job := workflow.Job{
		ID:            uuid.MustParse("0894481f-c096-49af-8d32-e9c531a66772"),
		RunID:         run.ID,
		JobType:       "model.pysteps_lk",
		ModelVersion:  "pysteps-lk-sim-v1",
		ConfigVersion: "rp003-sim-v1",
		Status:        workflow.JobRunning,
		Attempt:       1,
		CreatedAt:     now,
	}
	store := &fakeRunStore{run: run, jobs: []workflow.Job{job}}
	commands := &fakeRunCommands{run: workflow.Run{
		ID:            uuid.MustParse("47413539-2d55-41f7-8c22-788113dbeced"),
		IssueTime:     now,
		GridID:        run.GridID,
		ConfigVersion: run.ConfigVersion,
		Status:        workflow.RunBaselineRunning,
		CreatedAt:     now.Add(time.Minute),
		UpdatedAt:     now.Add(time.Minute),
	}}
	handler := api.NewHandler(api.Options{Version: "test", Runs: store, Commands: commands})

	assertStatus := func(method, target string, want int) *httptest.ResponseRecorder {
		t.Helper()
		request := httptest.NewRequest(method, target, nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != want {
			t.Fatalf("%s %s: got status %d, want %d; body=%s", method, target, response.Code, want, response.Body.String())
		}
		return response
	}

	latest := assertStatus(http.MethodGet, "/api/v1/runs/latest", http.StatusOK)
	if !strings.Contains(latest.Body.String(), `"status":"BASELINE_RUNNING"`) {
		t.Fatalf("latest run response has wrong state: %s", latest.Body.String())
	}
	jobs := assertStatus(http.MethodGet, "/api/v1/runs/"+run.ID.String()+"/jobs", http.StatusOK)
	if !strings.Contains(jobs.Body.String(), `"status":"RUNNING"`) {
		t.Fatalf("jobs response has wrong state: %s", jobs.Body.String())
	}
	rerun := assertStatus(http.MethodPost, "/api/v1/admin/runs/"+run.ID.String()+"/rerun", http.StatusAccepted)
	if !strings.Contains(rerun.Body.String(), commands.run.ID.String()) || commands.source != run.ID {
		t.Fatalf("rerun was not delegated correctly: %s", rerun.Body.String())
	}
}

func TestRunEventStreamSendsCurrentState(t *testing.T) {
	now := time.Date(2026, 8, 24, 3, 0, 0, 0, time.UTC)
	run := workflow.Run{
		ID:        uuid.MustParse("f3641335-13a3-4f68-96c0-56a5e0e684d7"),
		IssueTime: now, GridID: "grid", ConfigVersion: "config",
		Status: workflow.RunBaselineReady, CreatedAt: now, UpdatedAt: now,
	}
	handler := api.NewHandler(api.Options{
		Version: "test", Runs: &fakeRunStore{run: run}, SSEPollInterval: 5 * time.Millisecond,
	})
	server := httptest.NewServer(handler)
	defer server.Close()

	response, err := http.Get(server.URL + "/api/v1/events/stream?run_id=" + run.ID.String()) //nolint:noctx
	if err != nil {
		t.Fatalf("open SSE stream: %v", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || response.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("unexpected SSE response: status=%d content-type=%q", response.StatusCode, response.Header.Get("Content-Type"))
	}
	reader := bufio.NewReader(response.Body)
	var event strings.Builder
	for range 4 {
		line, err := reader.ReadString('\n')
		if err != nil {
			t.Fatalf("read SSE event: %v", err)
		}
		event.WriteString(line)
	}
	if !strings.Contains(event.String(), "event: run.updated") || !strings.Contains(event.String(), `"status":"BASELINE_READY"`) {
		t.Fatalf("unexpected SSE event: %s", event.String())
	}
}

type fakeRunStore struct {
	run  workflow.Run
	jobs []workflow.Job
	err  error
}

func (store *fakeRunStore) Ping(context.Context) error { return store.err }

func (store *fakeRunStore) LatestRun(context.Context) (workflow.Run, error) {
	return store.run, store.err
}

func (store *fakeRunStore) GetRun(_ context.Context, runID uuid.UUID) (workflow.Run, error) {
	if store.err != nil {
		return workflow.Run{}, store.err
	}
	if store.run.ID != runID {
		return workflow.Run{}, workflow.ErrNotFound
	}
	return store.run, nil
}

func (store *fakeRunStore) ListRuns(context.Context, int, *time.Time, *workflow.RunStatus) ([]workflow.Run, *time.Time, error) {
	return []workflow.Run{store.run}, nil, store.err
}

func (store *fakeRunStore) ListJobs(_ context.Context, runID uuid.UUID) ([]workflow.Job, error) {
	if runID != store.run.ID {
		return nil, workflow.ErrNotFound
	}
	return store.jobs, store.err
}

type fakeRunCommands struct {
	run    workflow.Run
	source uuid.UUID
}

func (commands *fakeRunCommands) Rerun(_ context.Context, source uuid.UUID) (workflow.Run, error) {
	commands.source = source
	return commands.run, nil
}

package workspace

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func newDataflowTestHandler(store RuntimeStore) http.Handler {
	core := http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		writeJSON(response, http.StatusOK, map[string]any{})
	})
	return NewRuntimeHandler(core, RuntimeOptions{Store: store})
}

func TestGetDataflowReturnsSnapshotWithWindowClamping(t *testing.T) {
	store := &runtimeFakeStore{}
	handler := newDataflowTestHandler(store)
	cases := []struct {
		raw    string
		window int
	}{
		{"", 60},
		{"window=30", 30},
		{"window=180", 180},
		{"window=5", 10},
		{"window=9999", 360},
		{"window=abc", 60},
	}
	for _, item := range cases {
		request := httptest.NewRequest(http.MethodGet, "/api/v1/workspace/dataflow?"+item.raw, nil)
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, request)
		if recorder.Code != http.StatusOK {
			t.Fatalf("window %q: status = %d", item.raw, recorder.Code)
		}
		if cache := recorder.Header().Get("Cache-Control"); cache != "no-store" {
			t.Fatalf("window %q: cache-control = %q", item.raw, cache)
		}
		var snapshot struct {
			SchemaVersion  string `json:"schema_version"`
			WindowMinutes  int    `json:"window_minutes"`
			Stages         []struct{ Key string } `json:"stages"`
			RadarLanes     []struct{ RadarID string } `json:"radar_lanes"`
			RadarStatuses  []struct{ RadarID string } `json:"radar_statuses"`
			GeneratedAt    string `json:"generated_at"`
		}
		if err := json.Unmarshal(recorder.Body.Bytes(), &snapshot); err != nil {
			t.Fatalf("window %q: decode snapshot: %v", item.raw, err)
		}
		if snapshot.SchemaVersion != "1.0" {
			t.Fatalf("window %q: schema version = %q", item.raw, snapshot.SchemaVersion)
		}
		if snapshot.WindowMinutes != item.window {
			t.Fatalf("window %q: window_minutes = %d; want %d", item.raw, snapshot.WindowMinutes, item.window)
		}
		if len(snapshot.Stages) != 7 || snapshot.Stages[0].Key != "ingest" {
			t.Fatalf("window %q: strip shape = %+v", item.raw, snapshot.Stages)
		}
	}
	if store.dataflowCalls != len(cases) {
		t.Fatalf("store calls = %d; want %d", store.dataflowCalls, len(cases))
	}
}

func TestGetDataflowReportsStoreErrors(t *testing.T) {
	store := &runtimeFakeStore{dataflowErr: context.DeadlineExceeded}
	handler := newDataflowTestHandler(store)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/workspace/dataflow", nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d; want 503", recorder.Code)
	}
	if !strings.Contains(recorder.Body.String(), "dataflow_snapshot_unavailable") {
		t.Fatalf("body = %s", recorder.Body.String())
	}
}

func TestGetDataflowHonorsHistoricalEndAnchor(t *testing.T) {
	store := &runtimeFakeStore{}
	handler := newDataflowTestHandler(store)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/workspace/dataflow?end=2026-08-28T08:30:00Z", nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d; want 200", recorder.Code)
	}
	var snapshot struct {
		WindowStart string `json:"window_start"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &snapshot); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(snapshot.WindowStart, "2026-08-28T07:30:00") {
		t.Fatalf("window_start = %q; want anchored at 07:30Z", snapshot.WindowStart)
	}
}

func TestGetDataflowRejectsInvalidEndAnchor(t *testing.T) {
	handler := newDataflowTestHandler(&runtimeFakeStore{})
	cases := []string{
		"end=2026-08-28+08:30", "end=not-a-time",
	}
	for _, query := range cases {
		request := httptest.NewRequest(http.MethodGet, "/api/v1/workspace/dataflow?"+query, nil)
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, request)
		if recorder.Code != http.StatusBadRequest {
			t.Fatalf("query %q: status = %d; want 400", query, recorder.Code)
		}
	}
	future := time.Now().UTC().Add(time.Hour).Format(time.RFC3339)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/workspace/dataflow?end="+future, nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("future end: status = %d; want 400", recorder.Code)
	}
}

func TestGetDataflowWithoutStoreIsUnavailable(t *testing.T) {
	handler := newDataflowTestHandler(nil)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/workspace/dataflow", nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d; want 503", recorder.Code)
	}
}

func TestStreamDataflowEventsSendsRevisionPing(t *testing.T) {
	t.Setenv("RAINPULSE_DATAFLOW_EVENT_INTERVAL", "150ms")
	store := &runtimeFakeStore{dataflowRevision: "rev-1"}
	handler := newDataflowTestHandler(store)
	ctx, cancel := context.WithCancel(context.Background())
	request := httptest.NewRequest(http.MethodGet, "/api/v1/workspace/dataflow/events", nil).WithContext(ctx)
	recorder := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		handler.ServeHTTP(recorder, request)
		close(done)
	}()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if strings.Contains(recorder.Body.String(), "event: dataflow.changed") {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	<-done
	body := recorder.Body.String()
	if !strings.Contains(recorder.Header().Get("Content-Type"), "text/event-stream") {
		t.Fatalf("content-type = %q", recorder.Header().Get("Content-Type"))
	}
	if !strings.Contains(body, ": connected") {
		t.Fatalf("missing connected comment: %q", body)
	}
	if !strings.Contains(body, "event: dataflow.changed") || !strings.Contains(body, `"revision":"rev-1"`) {
		t.Fatalf("missing revision ping: %q", body)
	}
}

package workspace

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestWorkspaceAnalysisPagingKeepsMorningAfter200Versions(t *testing.T) {
	calls := 0
	core := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/analysis-cycles":
			calls++
			items := []map[string]string{}
			next := ""
			if r.URL.Query().Get("cursor") == "" {
				for i := 0; i < 200; i++ {
					items = append(items, map[string]string{"analysis_id": fmt.Sprint(i), "analysis_time": "2026-08-28T08:45:00Z", "created_at": "2026-09-08T00:00:00Z", "grid_id": "fuzhou", "analysis_uri": "s3://test/qpe"})
				}
				next = "morning-page"
			} else {
				items = append(items, map[string]string{"analysis_id": "morning", "analysis_time": "2026-08-28T00:05:00Z", "grid_id": "fuzhou", "analysis_uri": "s3://test/morning"})
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"items": items, "next_cursor": next})
		case "/api/v1/ensemble-products/cycles":
			_, _ = w.Write([]byte(`[]`))
		default:
			_, _ = w.Write([]byte(`{"items":[]}`))
		}
	})
	handler := &Handler{core: core, now: func() time.Time { return time.Date(2026, 9, 8, 0, 0, 0, 0, time.UTC) }, catalogNeedsAnalysis: true}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, workspacePrefix, nil))
	var result cycleList
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if calls != 2 || len(result.Items) != 2 {
		t.Fatalf("analysis pages=%d, cycles=%d; morning must survive duplicate versions", calls, len(result.Items))
	}
	found := false
	for _, item := range result.Items {
		if item.AnalysisID == "morning" {
			found = true
		}
	}
	if !found {
		t.Fatal("morning analysis missing")
	}
}

func TestWorkspaceCycleListPagesPast200DistinctTimes(t *testing.T) {
	core := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/analysis-cycles" {
			items := []map[string]string{}
			for i := 0; i < 201; i++ {
				items = append(items, map[string]string{"analysis_id": fmt.Sprint(i), "analysis_time": time.Date(2026, 8, 28, 0, i*5, 0, 0, time.UTC).Format(time.RFC3339), "grid_id": "fuzhou", "analysis_uri": "s3://test/qpe"})
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"items": items})
		} else if r.URL.Path == "/api/v1/ensemble-products/cycles" {
			_, _ = w.Write([]byte(`[]`))
		} else {
			_, _ = w.Write([]byte(`{"items":[]}`))
		}
	})
	handler := &Handler{core: core, now: time.Now}
	read := func(path string) map[string]json.RawMessage {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
		var page map[string]json.RawMessage
		if err := json.Unmarshal(response.Body.Bytes(), &page); err != nil {
			t.Fatal(err)
		}
		return page
	}
	page := read(workspacePrefix + "?limit=200")
	var cursor string
	if err := json.Unmarshal(page["next_cursor"], &cursor); err != nil || cursor == "" {
		t.Fatal("missing cursor after 200 distinct cycles")
	}
	page = read(workspacePrefix + "?limit=200&cursor=" + cursor)
	var items []cycleSummary
	if err := json.Unmarshal(page["items"], &items); err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || items[0].IssueTime != "2026-08-28T00:00:00Z" {
		t.Fatalf("oldest cycle missing: %+v", items)
	}
}

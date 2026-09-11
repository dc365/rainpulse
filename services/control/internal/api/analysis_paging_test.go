package api_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/api"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

type pagedAnalyses struct {
	fakeObservationStore
	cursor *uuid.UUID
	limit  int
	status *workflow.AnalysisStatus
	next   uuid.UUID
}

func (s *pagedAnalyses) ListAnalysisCyclesPage(_ context.Context, limit int, status *workflow.AnalysisStatus, cursor *uuid.UUID) ([]workflow.AnalysisCycle, *uuid.UUID, error) {
	s.cursor, s.limit, s.status = cursor, limit, status
	return []workflow.AnalysisCycle{}, &s.next, nil
}
func TestAnalysisAPIForwardsKeysetCursor(t *testing.T) {
	store := &pagedAnalyses{next: uuid.New()}
	handler := api.NewHandler(api.Options{Observations: store})
	id := uuid.New()
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/v1/analysis-cycles?status=ANALYSIS_READY&limit=200&cursor="+id.String(), nil))
	if response.Code != 200 {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	if store.cursor == nil || *store.cursor != id || store.limit != 200 || store.status == nil || string(*store.status) != "ANALYSIS_READY" {
		t.Fatalf("query not forwarded: %+v", store)
	}
	var page struct {
		NextCursor uuid.UUID `json:"next_cursor"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &page); err != nil {
		t.Fatal(err)
	}
	if page.NextCursor != store.next {
		t.Fatalf("cursor=%s", page.NextCursor)
	}
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/v1/analysis-cycles?cursor=bad", nil))
	if response.Code != 400 {
		t.Fatalf("invalid cursor status=%d", response.Code)
	}
}

package workspace

import (
	"context"
	"errors"
	"net/http"
	"reflect"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/api"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/readquery"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

type batch3ViewStore struct {
	api.RunStore
	api.ObservationStore
	err error
}

func (s *batch3ViewStore) ListRuns(context.Context, int, *time.Time, *workflow.RunStatus) ([]workflow.Run, *time.Time, error) {
	at := time.Date(2026, 9, 22, 13, 0, 0, 123, time.UTC)
	return []workflow.Run{{ID: uuid.Nil, IssueTime: at, CreatedAt: at, GridID: "g", Status: workflow.RunStatus("PUBLISHED")}}, &at, s.err
}
func (s *batch3ViewStore) ListAnalysisCycles(context.Context, int, *workflow.AnalysisStatus) ([]workflow.AnalysisCycle, error) {
	c, e := s.GetAnalysisCycle(context.Background(), uuid.Nil)
	return []workflow.AnalysisCycle{c}, e
}
func (s *batch3ViewStore) ListAnalysisCyclesPage(context.Context, int, *workflow.AnalysisStatus, *uuid.UUID) ([]workflow.AnalysisCycle, *uuid.UUID, error) {
	c, e := s.GetAnalysisCycle(context.Background(), uuid.Nil)
	id := uuid.Nil
	return []workflow.AnalysisCycle{c}, &id, e
}
func (s *batch3ViewStore) GetAnalysisCycle(context.Context, uuid.UUID) (workflow.AnalysisCycle, error) {
	q := .123456789
	at := time.Date(2026, 9, 22, 13, 0, 0, 0, time.UTC)
	uri := "s3://rainpulse/a"
	reason := "partial"
	return workflow.AnalysisCycle{ID: uuid.Nil, RunID: uuid.Nil, AnalysisTime: at, CreatedAt: at, GridID: "g", Status: workflow.AnalysisReady, AnalysisURI: &uri, ValidCoverageRatio: &q, DegradedReason: &reason,
		Radars: []workflow.AnalysisRadar{{RadarID: "z1", State: workflow.AnalysisRadarMissing, MeanQualityIndex: &q}}}, s.err
}
func (s *batch3ViewStore) GetAnalysisQPEMetrics(context.Context, uuid.UUID) (workflow.AnalysisQPEMetrics, error) {
	return workflow.AnalysisQPEMetrics{AnalysisID: uuid.Nil, AnalysisTime: time.Date(2026, 9, 22, 13, 0, 0, 0, time.UTC), GridID: "g", QPEConfigVersion: "q1", MeanQualityIndex: .123456789}, s.err
}
func (s *batch3ViewStore) GetAnalysisDiagnostics(context.Context, uuid.UUID) (workflow.AnalysisDiagnostics, error) {
	e := .123456789
	b := []float64{118.123456789, 25, 123, 27}
	v := 10.
	return workflow.AnalysisDiagnostics{JobID: uuid.Nil, Manifest: workflow.DiagnosticManifest{AnalysisTime: time.Date(2026, 9, 22, 13, 0, 0, 0, time.UTC), Layers: []workflow.DiagnosticLayer{
		{LayerID: "a", Field: "DBZH_QC", ElevationDeg: &e, Bounds: b, Legend: []workflow.DiagnosticLegendEntry{{Label: "ten", Value: &v}}},
	}}}, s.err
}
func TestBatch3NativeReadParityWithActualAPI(t *testing.T) {
	store := &batch3ViewStore{}
	core := api.NewHandler(api.Options{Runs: store, Observations: store})
	legacy := newHandler(core, nil).(*Handler)
	calls := 0
	direct := newHandlerWithQueries(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls++; t.Error("native core read fallback") }), nil, readquery.New(store, store)).(*Handler)
	ctx := context.Background()
	id := uuid.Nil.String()
	tests := map[string]func(*Handler) (any, error){
		"run-page":      func(h *Handler) (any, error) { return h.queryForecastPage(ctx, "PUBLISHED", "") },
		"analysis-page": func(h *Handler) (any, error) { return h.queryAnalysisPage(ctx, "") },
		"analysis":      func(h *Handler) (any, error) { return h.queryAnalysis(ctx, id) },
		"qpe":           func(h *Handler) (any, error) { return h.queryQPE(ctx, id) },
		"diagnostics":   func(h *Handler) (any, error) { return h.queryDiagnostics(ctx, id) },
	}
	for name, query := range tests {
		t.Run(name, func(t *testing.T) {
			want, err := query(legacy)
			if err != nil {
				t.Fatal(err)
			}
			got, err := query(direct)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("typed=%#v legacy=%#v", got, want)
			}
		})
	}
	store.err = errors.New("store offline")
	for _, query := range tests {
		if _, e := query(direct); !errors.Is(e, store.err) {
			t.Fatal(e)
		}
	}
	if calls != 0 {
		t.Fatalf("%d silent HTTP fallback calls", calls)
	}
}

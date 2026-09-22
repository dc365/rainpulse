package readquery

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

type batch3Runs struct {
	calls  int
	limit  int
	cursor *time.Time
	status *workflow.RunStatus
	err    error
}

func (f *batch3Runs) GetRun(context.Context, uuid.UUID) (workflow.Run, error) {
	f.calls++
	return workflow.Run{}, f.err
}
func (f *batch3Runs) LatestRun(context.Context) (workflow.Run, error) {
	f.calls++
	return workflow.Run{}, f.err
}
func (f *batch3Runs) ListRuns(_ context.Context, limit int, cursor *time.Time, status *workflow.RunStatus) ([]workflow.Run, *time.Time, error) {
	f.calls++
	f.limit = limit
	f.cursor = cursor
	f.status = status
	return []workflow.Run{{GridID: "grid"}}, cursor, f.err
}

type batch3Analyses struct {
	calls int
	err   error
}

func (f *batch3Analyses) ListAnalysisCycles(context.Context, int, *workflow.AnalysisStatus) ([]workflow.AnalysisCycle, error) {
	f.calls++
	return []workflow.AnalysisCycle{{GridID: "grid"}}, f.err
}
func (f *batch3Analyses) GetAnalysisCycle(context.Context, uuid.UUID) (workflow.AnalysisCycle, error) {
	f.calls++
	return workflow.AnalysisCycle{}, f.err
}
func (f *batch3Analyses) GetAnalysisQPEMetrics(context.Context, uuid.UUID) (workflow.AnalysisQPEMetrics, error) {
	f.calls++
	return workflow.AnalysisQPEMetrics{}, f.err
}
func (f *batch3Analyses) GetAnalysisDiagnostics(context.Context, uuid.UUID) (workflow.AnalysisDiagnostics, error) {
	f.calls++
	return workflow.AnalysisDiagnostics{}, f.err
}

type batch3Paged struct {
	*batch3Analyses
	limit  int
	cursor *uuid.UUID
	status *workflow.AnalysisStatus
}

func (f *batch3Paged) ListAnalysisCyclesPage(_ context.Context, limit int, status *workflow.AnalysisStatus, cursor *uuid.UUID) ([]workflow.AnalysisCycle, *uuid.UUID, error) {
	f.calls++
	f.limit = limit
	f.status = status
	f.cursor = cursor
	return []workflow.AnalysisCycle{{GridID: "paged"}}, cursor, f.err
}

func TestBatch3RunPagePreservesArguments(t *testing.T) {
	f := &batch3Runs{}
	s := New(f, nil)
	stamp := time.Date(2026, 9, 22, 13, 0, 0, 123000, time.FixedZone("offset", 8*3600))
	status := workflow.RunStatus("PUBLISHED")
	items, next, err := s.ListRuns(context.Background(), 100, &stamp, &status)
	if err != nil || len(items) != 1 || next != &stamp || f.cursor != &stamp || f.status != &status || f.limit != 100 {
		t.Fatal(items, next, err, f)
	}
}
func TestBatch3AnalysisPagePreservesStableCursor(t *testing.T) {
	f := &batch3Paged{batch3Analyses: &batch3Analyses{}}
	s := New(nil, f)
	id := uuid.MustParse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
	status := workflow.AnalysisReady
	items, next, err := s.ListAnalysisCyclesPage(context.Background(), 200, &status, &id)
	if err != nil || len(items) != 1 || items[0].GridID != "paged" || next != &id || f.limit != 200 || f.status != &status {
		t.Fatal(items, next, err, f)
	}
}
func TestBatch3LegacyAnalysisStoreIsNotFakePagination(t *testing.T) {
	f := &batch3Analyses{}
	s := New(nil, f)
	id := uuid.Nil
	items, next, err := s.ListAnalysisCyclesPage(context.Background(), 100, nil, nil)
	if err != nil || next != nil || len(items) != 1 {
		t.Fatal(items, next, err)
	}
	_, _, err = s.ListAnalysisCyclesPage(context.Background(), 100, nil, &id)
	if !errors.Is(err, ErrPaginationUnavailable) || f.calls != 1 {
		t.Fatal(err, f.calls)
	}
}
func TestBatch3LimitsBeforeDependency(t *testing.T) {
	for _, limit := range []int{-1, 0, 201, 10000} {
		t.Run(time.Duration(limit).String(), func(t *testing.T) {
			r := &batch3Runs{}
			a := &batch3Analyses{}
			s := New(r, a)
			if _, _, err := s.ListRuns(context.Background(), limit, nil, nil); err == nil {
				t.Fatal("invalid run limit")
			}
			if _, _, err := s.ListAnalysisCyclesPage(context.Background(), limit, nil, nil); err == nil {
				t.Fatal("invalid analysis limit")
			}
			if r.calls+a.calls != 0 {
				t.Fatal("dependency called before validation")
			}
		})
	}
}
func TestBatch3CancellationAndUnavailable(t *testing.T) {
	methods := map[string]func(*Service, context.Context) error{
		"getRun":    func(s *Service, c context.Context) error { _, e := s.GetRun(c, uuid.Nil); return e },
		"latestRun": func(s *Service, c context.Context) error { _, e := s.LatestRun(c); return e },
		"runs":      func(s *Service, c context.Context) error { _, _, e := s.ListRuns(c, 100, nil, nil); return e },
		"analyses": func(s *Service, c context.Context) error {
			_, _, e := s.ListAnalysisCyclesPage(c, 100, nil, nil)
			return e
		},
		"analysis":    func(s *Service, c context.Context) error { _, e := s.GetAnalysisCycle(c, uuid.Nil); return e },
		"qpe":         func(s *Service, c context.Context) error { _, e := s.GetAnalysisQPEMetrics(c, uuid.Nil); return e },
		"diagnostics": func(s *Service, c context.Context) error { _, e := s.GetAnalysisDiagnostics(c, uuid.Nil); return e },
	}
	for name, fn := range methods {
		t.Run(name, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			cancel()
			r := &batch3Runs{}
			a := &batch3Analyses{}
			if err := fn(New(r, a), ctx); !errors.Is(err, context.Canceled) {
				t.Fatal(err)
			}
			if r.calls+a.calls != 0 {
				t.Fatal("canceled query reached store")
			}
			if err := fn(New(nil, nil), context.Background()); !errors.Is(err, ErrUnavailable) {
				t.Fatal(err)
			}
			if err := fn(nil, context.Background()); !errors.Is(err, ErrUnavailable) {
				t.Fatal(err)
			}
		})
	}
}
func TestBatch3StoreErrorsAreNotHidden(t *testing.T) {
	sentinel := errors.New("store sentinel")
	s := New(&batch3Runs{err: sentinel}, &batch3Analyses{err: sentinel})
	if _, err := s.GetRun(context.Background(), uuid.Nil); !errors.Is(err, sentinel) {
		t.Fatal(err)
	}
	if _, err := s.GetAnalysisDiagnostics(context.Background(), uuid.Nil); !errors.Is(err, sentinel) {
		t.Fatal(err)
	}
}

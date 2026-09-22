// Package readquery provides shared, typed domain reads for API and workspace.
// It owns no HTTP handlers, SQL, environment, mutable cache or workflow writes.
package readquery

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

var ErrUnavailable = errors.New("domain read service unavailable")
var ErrPaginationUnavailable = errors.New("analysis pagination unavailable")

type RunReader interface {
	GetRun(context.Context, uuid.UUID) (workflow.Run, error)
	LatestRun(context.Context) (workflow.Run, error)
	ListRuns(context.Context, int, *time.Time, *workflow.RunStatus) ([]workflow.Run, *time.Time, error)
}

type AnalysisReader interface {
	ListAnalysisCycles(context.Context, int, *workflow.AnalysisStatus) ([]workflow.AnalysisCycle, error)
	GetAnalysisCycle(context.Context, uuid.UUID) (workflow.AnalysisCycle, error)
	GetAnalysisQPEMetrics(context.Context, uuid.UUID) (workflow.AnalysisQPEMetrics, error)
	GetAnalysisDiagnostics(context.Context, uuid.UUID) (workflow.AnalysisDiagnostics, error)
}

type AnalysisPageReader interface {
	ListAnalysisCyclesPage(context.Context, int, *workflow.AnalysisStatus, *uuid.UUID) ([]workflow.AnalysisCycle, *uuid.UUID, error)
}

type Service struct {
	runs     RunReader
	analyses AnalysisReader
}

func New(runs RunReader, analyses AnalysisReader) *Service {
	return &Service{runs: runs, analyses: analyses}
}

func check(ctx context.Context, limit int) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if limit < 1 || limit > 200 {
		return fmt.Errorf("list limit must be between 1 and 200")
	}
	return nil
}

func (s *Service) GetRun(ctx context.Context, id uuid.UUID) (workflow.Run, error) {
	if err := ctx.Err(); err != nil {
		return workflow.Run{}, err
	}
	if s == nil || s.runs == nil {
		return workflow.Run{}, ErrUnavailable
	}
	return s.runs.GetRun(ctx, id)
}

func (s *Service) LatestRun(ctx context.Context) (workflow.Run, error) {
	if err := ctx.Err(); err != nil {
		return workflow.Run{}, err
	}
	if s == nil || s.runs == nil {
		return workflow.Run{}, ErrUnavailable
	}
	return s.runs.LatestRun(ctx)
}

// Preserve the existing API cursor contract. This is not the batch-1 planner query.
func (s *Service) ListRuns(ctx context.Context, limit int, cursor *time.Time, status *workflow.RunStatus) ([]workflow.Run, *time.Time, error) {
	if err := check(ctx, limit); err != nil {
		return nil, nil, err
	}
	if s == nil || s.runs == nil {
		return nil, nil, ErrUnavailable
	}
	return s.runs.ListRuns(ctx, limit, cursor, status)
}

func (s *Service) ListAnalysisCyclesPage(ctx context.Context, limit int, status *workflow.AnalysisStatus, cursor *uuid.UUID) ([]workflow.AnalysisCycle, *uuid.UUID, error) {
	if err := check(ctx, limit); err != nil {
		return nil, nil, err
	}
	if s == nil || s.analyses == nil {
		return nil, nil, ErrUnavailable
	}
	if paged, ok := s.analyses.(AnalysisPageReader); ok {
		return paged.ListAnalysisCyclesPage(ctx, limit, status, cursor)
	}
	if cursor != nil {
		return nil, nil, ErrPaginationUnavailable
	}
	items, err := s.analyses.ListAnalysisCycles(ctx, limit, status)
	return items, nil, err
}

func (s *Service) GetAnalysisCycle(ctx context.Context, id uuid.UUID) (workflow.AnalysisCycle, error) {
	if err := ctx.Err(); err != nil {
		return workflow.AnalysisCycle{}, err
	}
	if s == nil || s.analyses == nil {
		return workflow.AnalysisCycle{}, ErrUnavailable
	}
	return s.analyses.GetAnalysisCycle(ctx, id)
}

func (s *Service) GetAnalysisQPEMetrics(ctx context.Context, id uuid.UUID) (workflow.AnalysisQPEMetrics, error) {
	if err := ctx.Err(); err != nil {
		return workflow.AnalysisQPEMetrics{}, err
	}
	if s == nil || s.analyses == nil {
		return workflow.AnalysisQPEMetrics{}, ErrUnavailable
	}
	return s.analyses.GetAnalysisQPEMetrics(ctx, id)
}

func (s *Service) GetAnalysisDiagnostics(ctx context.Context, id uuid.UUID) (workflow.AnalysisDiagnostics, error) {
	if err := ctx.Err(); err != nil {
		return workflow.AnalysisDiagnostics{}, err
	}
	if s == nil || s.analyses == nil {
		return workflow.AnalysisDiagnostics{}, ErrUnavailable
	}
	return s.analyses.GetAnalysisDiagnostics(ctx, id)
}

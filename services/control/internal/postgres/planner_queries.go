package postgres

import (
	"context"
	"fmt"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/planning"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
)

// These reads intentionally do not change public list ordering/cursors. The
// caller supplies the entire scope before LIMIT and carries a value cursor.
func (store *Store) ListPlannerRadarScans(ctx context.Context, scope planning.Scope, after *planning.Cursor) ([]workflow.RadarScan, error) {
	suffix, args, err := planning.Predicate("radar", scope, after, planning.PageSize)
	if err != nil {
		return nil, err
	}
	rows, err := store.pool.Query(ctx, radarScanSelect+suffix, args...)
	if err != nil {
		return nil, fmt.Errorf("query bounded planner scans: %w", err)
	}
	defer rows.Close()
	result := make([]workflow.RadarScan, 0, planning.PageSize)
	for rows.Next() {
		item, err := scanRadarScan(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("read planner scans: %w", err)
	}
	return result, nil
}

func (store *Store) ListPlannerAnalyses(ctx context.Context, scope planning.Scope, after *planning.Cursor) ([]workflow.AnalysisCycle, error) {
	suffix, args, err := planning.Predicate("analysis", scope, after, planning.PageSize)
	if err != nil {
		return nil, err
	}
	rows, err := store.pool.Query(ctx, analysisSelect+suffix, args...)
	if err != nil {
		return nil, fmt.Errorf("query bounded planner analyses: %w", err)
	}
	defer rows.Close()
	result := make([]workflow.AnalysisCycle, 0, planning.PageSize)
	for rows.Next() {
		item, err := scanAnalysis(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("read planner analyses: %w", err)
	}
	// Planner consumes IDs/metadata only. Command handlers already fetch the
	// authoritative full input; avoid a redundant radar lookup per result.
	return result, nil
}

func (store *Store) ListPlannerRuns(ctx context.Context, scope planning.Scope, after *planning.Cursor) ([]workflow.Run, error) {
	suffix, args, err := planning.Predicate("forecast", scope, after, planning.PageSize)
	if err != nil {
		return nil, err
	}
	rows, err := store.pool.Query(ctx, runSelect+suffix, args...)
	if err != nil {
		return nil, fmt.Errorf("query bounded planner forecasts: %w", err)
	}
	defer rows.Close()
	result := make([]workflow.Run, 0, planning.PageSize)
	for rows.Next() {
		item, err := scanRun(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("read planner forecasts: %w", err)
	}
	return result, nil
}

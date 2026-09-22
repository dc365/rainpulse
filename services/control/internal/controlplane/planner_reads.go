package controlplane

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/planning"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/releaseguard"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
)

// Snapshot one finite live window per traversal. A separate explicit historical
// window is a union, never an excuse to query the intervening months/years.
func (p *pipelinePlanner) planningWindows(verification bool) ([]planning.Window, error) {
	now := time.Now().UTC()
	lookback := p.settings.lookback
	if lookback <= 0 {
		return nil, fmt.Errorf("automatic planner requires a positive lookback; use an explicit historical replay window instead of lookback=0")
	}
	if verification && lookback < 6*time.Hour {
		lookback = 6 * time.Hour
	}
	// Preserve nearby rounded volume/cycle timestamps but reject unbounded
	// future data. The lookahead is scheduling tolerance, not forecast input.
	lookahead := 6*time.Minute + p.settings.maximumMosaicOffset
	windows := []planning.Window{{Start: now.Add(-lookback), End: now.Add(lookahead)}}
	if !verification && p.settings.historicalReplayStart != nil && p.settings.historicalReplayEnd != nil {
		windows = append(windows, planning.Window{Start: *p.settings.historicalReplayStart, End: *p.settings.historicalReplayEnd})
	}
	return planning.NormalizeWindows(windows)
}

func (p *pipelinePlanner) plannerScope(status string, verification bool) (planning.Scope, error) {
	windows, err := p.planningWindows(verification)
	if err != nil {
		return planning.Scope{}, err
	}
	ids := make([]string, 0, len(p.settings.radarIDs))
	for id := range p.settings.radarIDs {
		ids = append(ids, id)
	}
	return planning.Scope{Windows: windows, RadarIDs: ids, GridID: p.settings.gridID, Status: status}, nil
}

func (p *pipelinePlanner) readPlanningScans(ctx context.Context, status workflow.RadarScanStatus) ([]workflow.RadarScan, error) {
	scope, err := p.plannerScope(string(status), false)
	if err != nil {
		return nil, err
	}
	scope.ExcludeQCOnly = status == workflow.RadarScanNormalized || status == workflow.RadarScanQCReady
	return p.readPlanningScansInScope(ctx, scope)
}

func (p *pipelinePlanner) readPlanningScansInScope(ctx context.Context, scope planning.Scope) ([]workflow.RadarScan, error) {
	result := make([]workflow.RadarScan, 0, planning.PageSize)
	var cursor *planning.Cursor
	for {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		pageCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		page, err := p.store.ListPlannerRadarScans(pageCtx, scope, cursor)
		cancel()
		if err != nil {
			return nil, err
		}
		result = append(result, page...)
		if len(page) < planning.PageSize {
			return result, nil
		}
		last := page[len(page)-1]
		next := planning.Cursor{Time: last.VolumeEndTime, ID: last.RunID.String()}
		if cursor != nil && *cursor == next {
			return nil, fmt.Errorf("planner radar cursor made no progress")
		}
		cursor = &next
	}
}

func (p *pipelinePlanner) listPlanningAnalyses(ctx context.Context, status workflow.AnalysisStatus) ([]workflow.AnalysisCycle, error) {
	scope, err := p.plannerScope(string(status), false)
	if err != nil {
		return nil, err
	}
	result := make([]workflow.AnalysisCycle, 0, planning.PageSize)
	var cursor *planning.Cursor
	for {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		pageCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		page, err := p.store.ListPlannerAnalyses(pageCtx, scope, cursor)
		cancel()
		if err != nil {
			return nil, err
		}
		result = append(result, page...)
		if len(page) < planning.PageSize {
			return result, nil
		}
		last := page[len(page)-1]
		next := planning.Cursor{Time: last.AnalysisTime, ID: last.ID.String()}
		if cursor != nil && *cursor == next {
			return nil, fmt.Errorf("planner analysis cursor made no progress")
		}
		cursor = &next
	}
}

func (p *pipelinePlanner) listPlanningRuns(ctx context.Context, status workflow.RunStatus) ([]workflow.Run, error) {
	scope, err := p.plannerScope(string(status), status == workflow.RunPublished)
	if err != nil {
		return nil, err
	}
	// Retain the existing explicit rerun exemption for compute/product stages;
	// verification keeps its independent six-hour truth lookback.
	scope.IncludeReruns = status != workflow.RunPublished
	result := make([]workflow.Run, 0, planning.PageSize)
	var cursor *planning.Cursor
	for {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		pageCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		page, err := p.store.ListPlannerRuns(pageCtx, scope, cursor)
		cancel()
		if err != nil {
			return nil, err
		}
		result = append(result, page...)
		if len(page) < planning.PageSize {
			return result, nil
		}
		last := page[len(page)-1]
		next := planning.Cursor{Time: last.IssueTime, ID: last.ID.String()}
		if cursor != nil && *cursor == next {
			return nil, fmt.Errorf("planner forecast cursor made no progress")
		}
		cursor = &next
	}
}

func (p *pipelinePlanner) regenerationScanWindow(request workflow.PipelineRegeneration) ([]planning.Window, error) {
	if len(request.Frames) == 0 {
		return nil, fmt.Errorf("regeneration must declare analysis frames")
	}
	start, end := request.Frames[0].AnalysisTime, request.Frames[0].AnalysisTime
	for _, frame := range request.Frames {
		if frame.AnalysisTime.IsZero() {
			return nil, fmt.Errorf("regeneration frame time is required")
		}
		if frame.AnalysisTime.Before(start) {
			start = frame.AnalysisTime
		}
		if frame.AnalysisTime.After(end) {
			end = frame.AnalysisTime
		}
	}
	// Both edges of the original absolute-duration test are inclusive.
	return planning.NormalizeWindows([]planning.Window{{Start: start.Add(-p.settings.maximumMosaicOffset), End: end.Add(p.settings.maximumMosaicOffset).Add(time.Microsecond)}})
}

func (p *pipelinePlanner) prunePlanningCaches(now time.Time) {
	const ttl = 2 * time.Hour
	const maximum = 8192
	planning.Prune(p.plannedQC, now, ttl, maximum)
	planning.Prune(p.plannedGrid, now, ttl, maximum)
	planning.Prune(p.plannedMosaic, now, ttl, maximum)
	planning.Prune(p.plannedQPE, now, ttl, maximum)
	planning.Prune(p.plannedDiagnostic, now, ttl, maximum)
	planning.Prune(p.plannedNowcast, now, ttl, maximum)
	planning.Prune(p.plannedPysteps, now, ttl, maximum)
	planning.Prune(p.plannedNowcastNet, now, ttl, maximum)
	planning.Prune(p.plannedProduct, now, ttl, maximum)
	planning.Prune(p.plannedVerification, now, ttl, maximum)
}

func (p *pipelinePlanner) beginPlanning(ctx context.Context) (func(), error) {
	if p.releaseQCHash == "" {
		hash, err := releaseguard.FileSHA256(p.settings.qcConfig)
		if err != nil {
			return nil, fmt.Errorf("freeze planner QC identity: %w", err)
		}
		p.releaseQCHash = hash
	}
	// Publish even while paused so resume can verify the resolved Go config.
	if err := releaseguard.PublishPlannerIdentity(p.settings.qcConfig, p.releaseQCHash, p.settings.mode); err != nil {
		return nil, err
	}
	release, err := releaseguard.Acquire(ctx)
	if errors.Is(err, releaseguard.ErrPaused) {
		return nil, nil
	}
	return release, err
}

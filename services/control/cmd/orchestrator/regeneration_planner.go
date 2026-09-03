package main

import (
	"context"
	"fmt"
	"sort"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

func (planner *pipelinePlanner) planPipelineRegenerations(ctx context.Context) error {
	requests, err := planner.store.ListActivePipelineRegenerations(ctx)
	if err != nil {
		return err
	}
	for _, request := range requests {
		if err := planner.planPipelineRegeneration(ctx, request); err != nil {
			message := err.Error()
			if statusErr := planner.store.UpdatePipelineRegenerationStatus(
				ctx,
				request.RequestID,
				request.Status,
				workflow.PipelineRegenerationFailed,
				&message,
			); statusErr != nil {
				return fmt.Errorf("fail pipeline regeneration after %v: %w", err, statusErr)
			}
		}
	}
	return nil
}

func (planner *pipelinePlanner) planPipelineRegeneration(
	ctx context.Context,
	request workflow.PipelineRegeneration,
) error {
	switch request.Status {
	case workflow.PipelineRegenerationPending:
		return planner.startRegeneratedQC(ctx, request)
	case workflow.PipelineRegenerationQCRunning:
		return planner.advanceRegeneratedQC(ctx, request)
	case workflow.PipelineRegenerationGridRunning:
		return planner.advanceRegeneratedGrid(ctx, request)
	case workflow.PipelineRegenerationMosaicRunning:
		return planner.advanceRegeneratedMosaic(ctx, request)
	case workflow.PipelineRegenerationQPERunning:
		return planner.advanceRegeneratedQPE(ctx, request)
	case workflow.PipelineRegenerationNowcastRunning:
		return planner.finishRegeneratedPipeline(ctx, request)
	default:
		return fmt.Errorf("unsupported pipeline regeneration status %q", request.Status)
	}
}

func (planner *pipelinePlanner) startRegeneratedQC(
	ctx context.Context,
	request workflow.PipelineRegeneration,
) error {
	for _, scan := range distinctRegenerationScans(request.Frames) {
		if _, _, err := createRadarQC(
			ctx, planner.store, planner.service, scan.ID.String(),
			planner.settings.qcConfig, request.RequestID,
		); err != nil {
			return fmt.Errorf("schedule regenerated QC for %s: %w", scan.ID, err)
		}
	}
	return planner.store.UpdatePipelineRegenerationStatus(
		ctx, request.RequestID, request.Status,
		workflow.PipelineRegenerationQCRunning, nil,
	)
}

func (planner *pipelinePlanner) advanceRegeneratedQC(
	ctx context.Context,
	request workflow.PipelineRegeneration,
) error {
	ready, err := planner.regenerationStageReady(
		ctx, request.RequestID, orchestration.RadarQCJobType,
		len(distinctRegenerationScans(request.Frames)),
	)
	if err != nil || !ready {
		return err
	}
	for _, scan := range distinctRegenerationScans(request.Frames) {
		if _, _, err := createRadarGrid(
			ctx, planner.store, planner.service, scan.ID.String(),
			planner.settings.gridConfig, request.RequestID,
		); err != nil {
			return fmt.Errorf("schedule regenerated grid for %s: %w", scan.ID, err)
		}
	}
	return planner.store.UpdatePipelineRegenerationStatus(
		ctx, request.RequestID, request.Status,
		workflow.PipelineRegenerationGridRunning, nil,
	)
}

func (planner *pipelinePlanner) advanceRegeneratedGrid(
	ctx context.Context,
	request workflow.PipelineRegeneration,
) error {
	ready, err := planner.regenerationStageReady(
		ctx, request.RequestID, orchestration.RadarGridJobType,
		len(distinctRegenerationScans(request.Frames)),
	)
	if err != nil || !ready {
		return err
	}
	for _, frame := range request.Frames {
		scanIDs := make([]string, 0, len(frame.Scans))
		for _, scan := range frame.Scans {
			scanIDs = append(scanIDs, scan.ID.String())
		}
		analysis, _, err := createAnalysisMosaic(
			ctx, planner.store, planner.service,
			frame.AnalysisTime.Format("2006-01-02T15:04:05.999999999Z07:00"),
			planner.settings.mosaicConfig, scanIDs, request.RequestID,
		)
		if err != nil {
			return fmt.Errorf("schedule regenerated mosaic for %s: %w", frame.AnalysisTime, err)
		}
		if err := planner.store.SetPipelineRegeneratedAnalysis(
			ctx, request.RequestID, frame.FrameIndex, analysis.ID,
		); err != nil {
			return err
		}
	}
	return planner.store.UpdatePipelineRegenerationStatus(
		ctx, request.RequestID, request.Status,
		workflow.PipelineRegenerationMosaicRunning, nil,
	)
}

func (planner *pipelinePlanner) advanceRegeneratedMosaic(
	ctx context.Context,
	request workflow.PipelineRegeneration,
) error {
	ready, err := planner.regenerationStageReady(
		ctx, request.RequestID, orchestration.AnalysisMosaicJobType, len(request.Frames),
	)
	if err != nil || !ready {
		return err
	}
	analyses, err := planner.store.ListPipelineRegenerationCandidates(ctx, request.RequestID)
	if err != nil {
		return err
	}
	if len(analyses) != len(request.Frames) {
		return fmt.Errorf("regenerated mosaic count differs from source frame count")
	}
	for _, analysis := range analyses {
		if _, _, err := createAnalysisQPE(
			ctx, planner.store, planner.service, analysis.ID.String(),
			planner.settings.qpeConfig, request.RequestID,
		); err != nil {
			return fmt.Errorf("schedule regenerated QPE for %s: %w", analysis.ID, err)
		}
	}
	return planner.store.UpdatePipelineRegenerationStatus(
		ctx, request.RequestID, request.Status,
		workflow.PipelineRegenerationQPERunning, nil,
	)
}

func (planner *pipelinePlanner) advanceRegeneratedQPE(
	ctx context.Context,
	request workflow.PipelineRegeneration,
) error {
	ready, err := planner.regenerationStageReady(
		ctx, request.RequestID, orchestration.AnalysisQPEJobType, len(request.Frames),
	)
	if err != nil || !ready {
		return err
	}
	analyses, err := planner.store.ListPipelineRegenerationCandidates(ctx, request.RequestID)
	if err != nil {
		return err
	}
	if len(analyses) != len(request.Frames) {
		return fmt.Errorf("regenerated QPE count differs from source frame count")
	}
	for _, analysis := range analyses {
		if _, _, err := createAnalysisDiagnostics(
			ctx, planner.store, planner.service, analysis.ID.String(),
			planner.settings.diagnosticConfig, request.RequestID,
		); err != nil {
			return fmt.Errorf("schedule regenerated diagnostics for %s: %w", analysis.ID, err)
		}
	}
	run, _, err := createRegeneratedNowcastInput(
		ctx, planner.service, request, analyses, planner.settings.nowcastConfig,
	)
	if err != nil {
		return fmt.Errorf("schedule regenerated NowcastInput: %w", err)
	}
	if run.ID != request.TargetRun {
		return fmt.Errorf("regenerated NowcastInput target identity changed")
	}
	return planner.store.UpdatePipelineRegenerationStatus(
		ctx, request.RequestID, request.Status,
		workflow.PipelineRegenerationNowcastRunning, nil,
	)
}

func (planner *pipelinePlanner) finishRegeneratedPipeline(
	ctx context.Context,
	request workflow.PipelineRegeneration,
) error {
	diagnosticsReady, err := planner.regenerationStageReady(
		ctx, request.RequestID, orchestration.AnalysisDiagnosticsJobType, len(request.Frames),
	)
	if err != nil || !diagnosticsReady {
		return err
	}
	run, err := planner.store.GetRun(ctx, request.TargetRun)
	if err != nil {
		return err
	}
	switch run.Status {
	case workflow.RunPublished, workflow.RunVerifying, workflow.RunVerified:
		return planner.store.UpdatePipelineRegenerationStatus(
			ctx, request.RequestID, request.Status,
			workflow.PipelineRegenerationSucceeded, nil,
		)
	case workflow.RunFailed, workflow.RunDegraded, workflow.RunSkipped:
		return fmt.Errorf("regenerated forecast finished with status %s", run.Status)
	default:
		return nil
	}
}

func (planner *pipelinePlanner) regenerationStageReady(
	ctx context.Context,
	requestID uuid.UUID,
	jobType string,
	expected int,
) (bool, error) {
	jobs, err := planner.store.ListPipelineRegenerationJobs(ctx, requestID, jobType)
	if err != nil {
		return false, err
	}
	if len(jobs) != expected {
		return false, nil
	}
	for _, job := range jobs {
		switch job.Status {
		case workflow.JobSucceeded:
			continue
		case workflow.JobFailed, workflow.JobSkipped:
			return false, fmt.Errorf("%s job %s finished with status %s", jobType, job.ID, job.Status)
		default:
			return false, nil
		}
	}
	return true, nil
}

func distinctRegenerationScans(
	frames []workflow.PipelineRegenerationFrame,
) []workflow.RadarScan {
	byID := make(map[uuid.UUID]workflow.RadarScan)
	for _, frame := range frames {
		for _, scan := range frame.Scans {
			byID[scan.ID] = scan
		}
	}
	items := make([]workflow.RadarScan, 0, len(byID))
	for _, scan := range byID {
		items = append(items, scan)
	}
	sort.Slice(items, func(left, right int) bool {
		return items[left].ID.String() < items[right].ID.String()
	})
	return items
}

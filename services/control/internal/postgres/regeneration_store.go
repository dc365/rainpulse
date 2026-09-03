package postgres

import (
	"context"
	"fmt"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func (store *Store) CreateFullPipelineRegeneration(
	ctx context.Context,
	request workflow.PipelineRegeneration,
	target workflow.Run,
) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin full pipeline regeneration: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	lockIdentity := request.SourceRun.String() + ":" + request.Preset
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`, lockIdentity); err != nil {
		return fmt.Errorf("lock full pipeline regeneration: %w", err)
	}
	var active bool
	if err = tx.QueryRow(ctx, `
SELECT EXISTS (
    SELECT 1 FROM forecast_runs
    WHERE rerun_of = $1
      AND reason LIKE $2
      AND status NOT IN ('PUBLISHED', 'VERIFIED', 'DEGRADED', 'FAILED', 'SKIPPED')
)`, request.SourceRun, "manual-regeneration/"+request.Preset+":%").Scan(&active); err != nil {
		return fmt.Errorf("check active full pipeline regeneration: %w", err)
	}
	if active {
		return orchestration.ErrRegenerationActive
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO workflow_runs (run_id, run_type, created_at)
VALUES ($1, 'forecast_run', $2)`, target.ID, target.CreatedAt); err != nil {
		return fmt.Errorf("insert full regeneration workflow identity: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO forecast_runs (
    run_id, issue_time, grid_id, config_version, status, rerun_of, reason,
    created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)`,
		target.ID, target.IssueTime, target.GridID, target.ConfigVersion,
		target.Status, target.RerunOf, target.Reason, target.CreatedAt); err != nil {
		return fmt.Errorf("insert full regeneration target run: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO pipeline_regeneration_requests (
    request_id, source_run_id, target_run_id, issue_time, grid_id,
    preset, reason, status, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)`,
		request.RequestID, request.SourceRun, request.TargetRun,
		request.IssueTime, request.GridID, request.Preset, request.Reason,
		request.Status, request.CreatedAt); err != nil {
		return fmt.Errorf("insert full pipeline regeneration request: %w", err)
	}
	result, err := tx.Exec(ctx, `
INSERT INTO pipeline_regeneration_frames (
    request_id, frame_index, source_analysis_id, analysis_time
)
SELECT $1, frame.frame_index, frame.analysis_id, frame.analysis_time
FROM nowcast_input_runs AS input
JOIN nowcast_input_frames AS frame ON frame.job_id = input.job_id
WHERE input.run_id = $2 AND input.status = 'SUCCEEDED'
ORDER BY frame.frame_index`, request.RequestID, request.SourceRun)
	if err != nil {
		return fmt.Errorf("copy full regeneration source frames: %w", err)
	}
	if count := result.RowsAffected(); count < 3 || count > 6 {
		return fmt.Errorf("full regeneration source must contain three to six committed frames")
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO pipeline_regeneration_frame_scans (
    request_id, frame_index, radar_id, scan_id
)
SELECT frame.request_id, frame.frame_index, radar.radar_id, radar.scan_id
FROM pipeline_regeneration_frames AS frame
JOIN analysis_cycle_radars AS radar
  ON radar.analysis_id = frame.source_analysis_id
 AND radar.state = 'PARTICIPATING'
WHERE frame.request_id = $1`, request.RequestID); err != nil {
		return fmt.Errorf("copy full regeneration source radar scans: %w", err)
	}
	var frameCount, frameWithScans int
	if err = tx.QueryRow(ctx, `
SELECT COUNT(*), COUNT(*) FILTER (WHERE EXISTS (
    SELECT 1 FROM pipeline_regeneration_frame_scans AS scan
    WHERE scan.request_id = frame.request_id AND scan.frame_index = frame.frame_index
))
FROM pipeline_regeneration_frames AS frame
WHERE frame.request_id = $1`, request.RequestID).Scan(&frameCount, &frameWithScans); err != nil {
		return fmt.Errorf("validate full regeneration radar lineage: %w", err)
	}
	if frameCount != frameWithScans {
		return fmt.Errorf("every full regeneration frame must retain a participating radar scan")
	}
	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit full pipeline regeneration: %w", err)
	}
	return nil
}

func (store *Store) ListActivePipelineRegenerations(
	ctx context.Context,
) ([]workflow.PipelineRegeneration, error) {
	rows, err := store.pool.Query(ctx, `
SELECT request_id, source_run_id, target_run_id, issue_time, grid_id,
       preset, reason, status, error_message, created_at, updated_at
FROM pipeline_regeneration_requests
WHERE status NOT IN ('SUCCEEDED', 'FAILED')
ORDER BY created_at
LIMIT 10`)
	if err != nil {
		return nil, fmt.Errorf("list active pipeline regenerations: %w", err)
	}
	defer rows.Close()
	items := make([]workflow.PipelineRegeneration, 0)
	for rows.Next() {
		var item workflow.PipelineRegeneration
		if err := rows.Scan(
			&item.RequestID, &item.SourceRun, &item.TargetRun, &item.IssueTime,
			&item.GridID, &item.Preset, &item.Reason, &item.Status, &item.Error,
			&item.CreatedAt, &item.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan active pipeline regeneration: %w", err)
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate active pipeline regenerations: %w", err)
	}
	rows.Close()
	for index := range items {
		items[index].Frames, err = store.loadPipelineRegenerationFrames(ctx, items[index].RequestID)
		if err != nil {
			return nil, err
		}
	}
	return items, nil
}

func (store *Store) loadPipelineRegenerationFrames(
	ctx context.Context,
	requestID uuid.UUID,
) ([]workflow.PipelineRegenerationFrame, error) {
	rows, err := store.pool.Query(ctx, `
SELECT frame_index, source_analysis_id, analysis_time, regenerated_analysis_id
FROM pipeline_regeneration_frames
WHERE request_id = $1
ORDER BY frame_index`, requestID)
	if err != nil {
		return nil, fmt.Errorf("list pipeline regeneration frames: %w", err)
	}
	defer rows.Close()
	frames := make([]workflow.PipelineRegenerationFrame, 0, 6)
	for rows.Next() {
		var frame workflow.PipelineRegenerationFrame
		if err := rows.Scan(
			&frame.FrameIndex, &frame.SourceAnalysisID, &frame.AnalysisTime,
			&frame.RegeneratedAnalysisID,
		); err != nil {
			return nil, fmt.Errorf("scan pipeline regeneration frame: %w", err)
		}
		frames = append(frames, frame)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate pipeline regeneration frames: %w", err)
	}
	rows.Close()
	for index := range frames {
		scanRows, scanErr := store.pool.Query(ctx, radarScanSelect+`
JOIN pipeline_regeneration_frame_scans AS selected ON selected.scan_id = s.scan_id
WHERE selected.request_id = $1 AND selected.frame_index = $2
ORDER BY s.radar_id`, requestID, frames[index].FrameIndex)
		if scanErr != nil {
			return nil, fmt.Errorf("list pipeline regeneration scans: %w", scanErr)
		}
		for scanRows.Next() {
			scan, scanErr := scanRadarScan(scanRows)
			if scanErr != nil {
				scanRows.Close()
				return nil, scanErr
			}
			frames[index].Scans = append(frames[index].Scans, scan)
		}
		if scanErr := scanRows.Err(); scanErr != nil {
			scanRows.Close()
			return nil, fmt.Errorf("iterate pipeline regeneration scans: %w", scanErr)
		}
		scanRows.Close()
	}
	return frames, nil
}

func (store *Store) UpdatePipelineRegenerationStatus(
	ctx context.Context,
	requestID uuid.UUID,
	from workflow.PipelineRegenerationStatus,
	to workflow.PipelineRegenerationStatus,
	errorMessage *string,
) error {
	result, err := store.pool.Exec(ctx, `
UPDATE pipeline_regeneration_requests
SET status = $3, error_message = $4, updated_at = CURRENT_TIMESTAMP
WHERE request_id = $1 AND status = $2`, requestID, from, to, errorMessage)
	if err != nil {
		return fmt.Errorf("advance pipeline regeneration: %w", err)
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("pipeline regeneration status changed concurrently")
	}
	if to == workflow.PipelineRegenerationFailed {
		if _, err := store.pool.Exec(ctx, `
UPDATE forecast_runs AS target
SET status = 'FAILED', updated_at = CURRENT_TIMESTAMP
FROM pipeline_regeneration_requests AS request
WHERE request.request_id = $1 AND target.run_id = request.target_run_id
  AND target.status NOT IN ('PUBLISHED', 'VERIFIED', 'DEGRADED', 'FAILED', 'SKIPPED')`, requestID); err != nil {
			return fmt.Errorf("fail pipeline regeneration target run: %w", err)
		}
	}
	return nil
}

func (store *Store) SetPipelineRegeneratedAnalysis(
	ctx context.Context,
	requestID uuid.UUID,
	frameIndex int,
	analysisID uuid.UUID,
) error {
	result, err := store.pool.Exec(ctx, `
UPDATE pipeline_regeneration_frames
SET regenerated_analysis_id = $3
WHERE request_id = $1 AND frame_index = $2
  AND (regenerated_analysis_id IS NULL OR regenerated_analysis_id = $3)`,
		requestID, frameIndex, analysisID)
	if err != nil {
		return fmt.Errorf("record regenerated analysis: %w", err)
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("regenerated analysis identity changed")
	}
	return nil
}

func (store *Store) ListPipelineRegenerationJobs(
	ctx context.Context,
	requestID uuid.UUID,
	jobType string,
) ([]workflow.Job, error) {
	rows, err := store.pool.Query(ctx, jobSelect+`
WHERE j.regeneration_request_id = $1 AND j.job_type = $2
ORDER BY j.created_at`, requestID, jobType)
	if err != nil {
		return nil, fmt.Errorf("list pipeline regeneration jobs: %w", err)
	}
	defer rows.Close()
	items := make([]workflow.Job, 0)
	for rows.Next() {
		job, err := scanJob(rows)
		if err != nil {
			return nil, err
		}
		items = append(items, job)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate pipeline regeneration jobs: %w", err)
	}
	return items, nil
}

func (store *Store) ListPipelineRegenerationCandidates(
	ctx context.Context,
	requestID uuid.UUID,
) ([]workflow.AnalysisCycle, error) {
	rows, err := store.pool.Query(ctx, analysisSelect+`
JOIN pipeline_regeneration_frames AS frame ON frame.regenerated_analysis_id = a.analysis_id
WHERE frame.request_id = $1
ORDER BY frame.frame_index`, requestID)
	if err != nil {
		return nil, fmt.Errorf("list regenerated analyses: %w", err)
	}
	defer rows.Close()
	items := make([]workflow.AnalysisCycle, 0, 6)
	for rows.Next() {
		cycle, err := scanAnalysis(rows)
		if err != nil {
			return nil, err
		}
		items = append(items, cycle)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate regenerated analyses: %w", err)
	}
	return items, nil
}

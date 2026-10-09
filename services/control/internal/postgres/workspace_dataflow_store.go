package postgres

import (
	"context"
	"fmt"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workspace"
)

// dataflowScanRows joins radar scans, their workflow runs and automatic-lane
// jobs inside one wall-clock window. Queue time stays visible because job
// created_at precedes started_at.
const dataflowScanQuery = `
SELECT s.radar_id, s.scan_id::text, r.run_id::text,
       s.volume_start_time, s.volume_end_time, s.received_at,
       r.status, COALESCE(r.degraded_reason, ''),
       r.scan_completeness, r.mean_quality_index,
       COALESCE(j.job_id::text, ''), COALESCE(j.job_type, ''),
       COALESCE(j.status, ''), COALESCE(j.model_id, ''),
       j.created_at, j.started_at, j.completed_at,
       COALESCE(attempt.error_code, ''), COALESCE(attempt.error_message, '')
FROM radar_scans AS s
JOIN radar_scan_runs AS r ON r.scan_id = s.scan_id
LEFT JOIN jobs AS j ON j.run_id = r.run_id
LEFT JOIN LATERAL (
    SELECT a.error_code, a.error_message
    FROM job_attempts AS a
    WHERE a.job_id = j.job_id
    ORDER BY a.attempt_no DESC LIMIT 1
) AS attempt ON TRUE
WHERE s.volume_end_time >= $1 AND s.volume_end_time < $2
ORDER BY s.radar_id, s.volume_start_time, j.created_at
LIMIT 5000`

// dataflowAnalysisQuery keeps only automatic analysis cycles: cycles whose
// mosaic job belongs to a manual regeneration are excluded, mirroring the
// automatic analysis listing.
const dataflowAnalysisQuery = `
SELECT a.analysis_id::text, a.run_id::text, a.analysis_time, a.grid_id, a.status,
       COALESCE(a.degraded_reason, ''), a.radar_count, a.valid_coverage_ratio, a.created_at,
       COALESCE(j.job_id::text, ''), COALESCE(j.job_type, ''),
       COALESCE(j.status, ''), COALESCE(j.model_id, ''),
       j.created_at, j.started_at, j.completed_at,
       COALESCE(attempt.error_code, ''), COALESCE(attempt.error_message, '')
FROM analysis_cycles AS a
LEFT JOIN jobs AS j
       ON j.run_id = a.run_id AND j.regeneration_request_id IS NULL
LEFT JOIN LATERAL (
    SELECT a2.error_code, a2.error_message
    FROM job_attempts AS a2
    WHERE a2.job_id = j.job_id
    ORDER BY a2.attempt_no DESC LIMIT 1
) AS attempt ON TRUE
WHERE a.analysis_time >= $1 AND a.analysis_time < $2
  AND NOT EXISTS (
      SELECT 1 FROM mosaic_runs AS m JOIN jobs AS rj ON rj.job_id = m.job_id
      WHERE m.analysis_id = a.analysis_id AND rj.regeneration_request_id IS NOT NULL
  )
ORDER BY a.analysis_time, j.created_at
LIMIT 2000`

// dataflowForecastQuery keeps only original forecast runs; manual
// regenerations (rerun_of) stay on the admin console.
const dataflowForecastQuery = `
SELECT f.run_id::text, f.issue_time, f.grid_id, f.status, f.created_at, f.updated_at,
       COALESCE(j.job_id::text, ''), COALESCE(j.job_type, ''),
       COALESCE(j.status, ''), COALESCE(j.model_id, ''),
       j.created_at, j.started_at, j.completed_at,
       COALESCE(attempt.error_code, ''), COALESCE(attempt.error_message, '')
FROM forecast_runs AS f
LEFT JOIN jobs AS j
       ON j.run_id = f.run_id AND j.regeneration_request_id IS NULL
LEFT JOIN LATERAL (
    SELECT a.error_code, a.error_message
    FROM job_attempts AS a
    WHERE a.job_id = j.job_id
    ORDER BY a.attempt_no DESC LIMIT 1
) AS attempt ON TRUE
WHERE f.issue_time >= $1 AND f.issue_time < $2 AND f.rerun_of IS NULL
ORDER BY f.issue_time, j.created_at
LIMIT 2000`

const dataflowRevisionQuery = `
SELECT COALESCE((SELECT MAX(updated_at)::text FROM radar_scan_runs), '') || '|' ||
       COALESCE((SELECT MAX(updated_at)::text FROM jobs), '') || '|' ||
       COALESCE((SELECT MAX(created_at)::text FROM radar_scans), '') || '|' ||
       COALESCE((SELECT MAX(updated_at)::text FROM analysis_cycles), '') || '|' ||
       COALESCE((SELECT MAX(updated_at)::text FROM forecast_runs), '')`

// WorkspaceDataflowSnapshot collects the automatic pipeline inside the wall
// clock window and hands the rows to the pure workspace assembler. The anchor
// is the window's right edge: live follow passes the current time, historical
// replay passes the requested moment. Scan and radar-status reads are
// authoritative; analysis and forecast reads degrade to a warning instead of
// failing the whole screen.
func (store *Store) WorkspaceDataflowSnapshot(
	ctx context.Context,
	anchor time.Time,
	window time.Duration,
) (workspace.DataflowSnapshot, error) {
	if window < 10*time.Minute || window > 6*time.Hour {
		return workspace.DataflowSnapshot{}, fmt.Errorf("dataflow window must be between 10 minutes and 6 hours")
	}
	cutoff := anchor.UTC().Add(-window)
	scans, err := store.dataflowScanRows(ctx, cutoff, anchor.UTC())
	if err != nil {
		return workspace.DataflowSnapshot{}, err
	}
	analyses, analysisErr := store.dataflowAnalysisRows(ctx, cutoff, anchor.UTC())
	warnings := make([]string, 0, 2)
	if analysisErr != nil {
		warnings = append(warnings, "analysis-cycles")
	}
	forecasts, forecastErr := store.dataflowForecastRows(ctx, cutoff, anchor.UTC())
	if forecastErr != nil {
		warnings = append(warnings, "forecast-runs")
	}
	statuses, statusErr := store.ListRadarStatuses(ctx)
	if statusErr != nil {
		return workspace.DataflowSnapshot{}, fmt.Errorf("read radar statuses for dataflow: %w", statusErr)
	}
	return workspace.AssembleDataflowSnapshot(anchor, time.Now().UTC(), window, scans, analyses, forecasts, statuses, warnings), nil
}

// WorkspaceDataflowRevision is the cheap change token behind the SSE stream:
// the newest update timestamps of exactly the tables the screen renders.
func (store *Store) WorkspaceDataflowRevision(ctx context.Context) (string, error) {
	var revision string
	if err := store.pool.QueryRow(ctx, dataflowRevisionQuery).Scan(&revision); err != nil {
		return "", fmt.Errorf("read dataflow revision: %w", err)
	}
	return revision, nil
}

func (store *Store) dataflowScanRows(ctx context.Context, cutoff, anchor time.Time) ([]workspace.DataflowScanBlock, error) {
	rows, err := store.pool.Query(ctx, dataflowScanQuery, cutoff, anchor)
	if err != nil {
		return nil, fmt.Errorf("query dataflow scan rows: %w", err)
	}
	defer rows.Close()
	blocks := make([]workspace.DataflowScanBlock, 0)
	indexByRun := make(map[string]int)
	for rows.Next() {
		var block workspace.DataflowScanBlock
		stage, ok, err := scanDataflowScanRow(rows, &block)
		if err != nil {
			return nil, err
		}
		if index, seen := indexByRun[block.RunID]; seen {
			if ok {
				blocks[index].Stages = append(blocks[index].Stages, stage)
			}
			continue
		}
		block.Stages = dataflowInitialStages(stage, ok)
		indexByRun[block.RunID] = len(blocks)
		blocks = append(blocks, block)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate dataflow scan rows: %w", err)
	}
	return blocks, nil
}

func scanDataflowScanRow(row rowScanner, block *workspace.DataflowScanBlock) (workspace.DataflowJobStage, bool, error) {
	var job jobRowParts
	err := row.Scan(
		&block.RadarID, &block.ScanID, &block.RunID,
		&block.VolumeStart, &block.VolumeEnd, &block.ReceivedAt,
		&block.Status, &block.DegradedReason,
		&block.ScanCompleteness, &block.MeanQualityIndex,
		&job.ID, &job.Type, &job.Status, &job.ModelID,
		&job.Created, &job.Started, &job.Finished,
		&job.ErrorCode, &job.ErrorMessage,
	)
	if err != nil {
		return workspace.DataflowJobStage{}, false, fmt.Errorf("scan dataflow scan row: %w", err)
	}
	stage, ok := job.stage()
	return stage, ok, nil
}

func (store *Store) dataflowAnalysisRows(ctx context.Context, cutoff, anchor time.Time) ([]workspace.DataflowAnalysisBlock, error) {
	rows, err := store.pool.Query(ctx, dataflowAnalysisQuery, cutoff, anchor)
	if err != nil {
		return nil, fmt.Errorf("query dataflow analysis rows: %w", err)
	}
	defer rows.Close()
	blocks := make([]workspace.DataflowAnalysisBlock, 0)
	indexByRun := make(map[string]int)
	for rows.Next() {
		var block workspace.DataflowAnalysisBlock
		var job jobRowParts
		err := rows.Scan(
			&block.AnalysisID, &block.RunID, &block.AnalysisTime, &block.GridID,
			&block.Status, &block.DegradedReason, &block.RadarCount,
			&block.CoverageRatio, &block.CreatedAt,
			&job.ID, &job.Type, &job.Status, &job.ModelID,
			&job.Created, &job.Started, &job.Finished,
			&job.ErrorCode, &job.ErrorMessage,
		)
		if err != nil {
			return nil, fmt.Errorf("scan dataflow analysis row: %w", err)
		}
		stage, ok := job.stage()
		if index, seen := indexByRun[block.RunID]; seen {
			if ok {
				blocks[index].Stages = append(blocks[index].Stages, stage)
			}
			continue
		}
		block.Stages = dataflowInitialStages(stage, ok)
		indexByRun[block.RunID] = len(blocks)
		blocks = append(blocks, block)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate dataflow analysis rows: %w", err)
	}
	return blocks, nil
}

func (store *Store) dataflowForecastRows(ctx context.Context, cutoff, anchor time.Time) ([]workspace.DataflowForecastBlock, error) {
	rows, err := store.pool.Query(ctx, dataflowForecastQuery, cutoff, anchor)
	if err != nil {
		return nil, fmt.Errorf("query dataflow forecast rows: %w", err)
	}
	defer rows.Close()
	blocks := make([]workspace.DataflowForecastBlock, 0)
	indexByRun := make(map[string]int)
	for rows.Next() {
		var block workspace.DataflowForecastBlock
		var job jobRowParts
		err := rows.Scan(
			&block.RunID, &block.IssueTime, &block.GridID, &block.Status,
			&block.CreatedAt, &block.UpdatedAt,
			&job.ID, &job.Type, &job.Status, &job.ModelID,
			&job.Created, &job.Started, &job.Finished,
			&job.ErrorCode, &job.ErrorMessage,
		)
		if err != nil {
			return nil, fmt.Errorf("scan dataflow forecast row: %w", err)
		}
		stage, ok := job.stage()
		if index, seen := indexByRun[block.RunID]; seen {
			if ok {
				blocks[index].Stages = append(blocks[index].Stages, stage)
			}
			continue
		}
		block.Stages = dataflowInitialStages(stage, ok)
		indexByRun[block.RunID] = len(blocks)
		blocks = append(blocks, block)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate dataflow forecast rows: %w", err)
	}
	return blocks, nil
}

func dataflowInitialStages(stage workspace.DataflowJobStage, ok bool) []workspace.DataflowJobStage {
	if !ok {
		return []workspace.DataflowJobStage{}
	}
	return []workspace.DataflowJobStage{stage}
}

// jobRowParts holds the shared job columns of the three dataflow queries so
// the per-lane scanners stay in the column order of their SQL.
type jobRowParts struct {
	ID           string
	Type         string
	Status       string
	ModelID      string
	Created      *time.Time
	Started      *time.Time
	Finished     *time.Time
	ErrorCode    string
	ErrorMessage string
}

func (job jobRowParts) stage() (workspace.DataflowJobStage, bool) {
	if job.ID == "" {
		return workspace.DataflowJobStage{}, false
	}
	stage := workspace.DataflowJobStage{
		Stage:        stageIdentity(job.Type),
		JobID:        job.ID,
		Status:       job.Status,
		StartedAt:    job.Started,
		FinishedAt:   job.Finished,
		ErrorCode:    job.ErrorCode,
		ErrorMessage: job.ErrorMessage,
		ModelID:      job.ModelID,
	}
	if job.Created != nil {
		stage.CreatedAt = *job.Created
	}
	if job.Finished != nil && job.Started != nil {
		runtime := job.Finished.Sub(*job.Started).Milliseconds()
		if runtime >= 0 {
			stage.RuntimeMS = &runtime
		}
	}
	return stage, true
}

// stageIdentity maps a job type onto the domain stage identity shared with the
// per-cycle pipeline snapshot.
func stageIdentity(jobType string) string {
	stage, _ := pipelineStageIdentity(jobType)
	return stage
}

// dataflowTrendQuery collects succeeded automatic-lane jobs with a computable
// runtime in milliseconds; bucketing and medians happen in the workspace
// assembler.
const dataflowTrendQuery = `
SELECT j.job_type, j.completed_at,
       (EXTRACT(EPOCH FROM (j.completed_at - j.started_at)) * 1000)::bigint AS runtime_ms
FROM jobs AS j
WHERE j.status = 'SUCCEEDED' AND j.completed_at >= $1
  AND j.started_at IS NOT NULL AND j.regeneration_request_id IS NULL
LIMIT 50000`

// WorkspaceDataflowStageTrend builds the per-stage median-runtime history for
// the sparkline panel. The window always spans the requested hours regardless
// of the dataflow display window.
func (store *Store) WorkspaceDataflowStageTrend(
	ctx context.Context,
	now time.Time,
	hours int,
) (workspace.DataflowStageTrend, error) {
	if hours < 1 || hours > 24 {
		return workspace.DataflowStageTrend{}, fmt.Errorf("stage trend hours must be between 1 and 24")
	}
	rows, err := store.pool.Query(ctx, dataflowTrendQuery, now.UTC().Add(-time.Duration(hours)*time.Hour))
	if err != nil {
		return workspace.DataflowStageTrend{}, fmt.Errorf("query dataflow trend rows: %w", err)
	}
	defer rows.Close()
	samples := make([]workspace.DataflowStageRuntimeSample, 0, 1024)
	for rows.Next() {
		var jobType string
		var finished time.Time
		var runtimeMS int64
		if err := rows.Scan(&jobType, &finished, &runtimeMS); err != nil {
			return workspace.DataflowStageTrend{}, fmt.Errorf("scan dataflow trend row: %w", err)
		}
		if runtimeMS < 0 {
			continue
		}
		samples = append(samples, workspace.DataflowStageRuntimeSample{
			Group:      workspace.DataflowStripGroup(stageIdentity(jobType)),
			FinishedAt: finished,
			RuntimeMS:  runtimeMS,
		})
	}
	if err := rows.Err(); err != nil {
		return workspace.DataflowStageTrend{}, fmt.Errorf("iterate dataflow trend rows: %w", err)
	}
	return workspace.BuildDataflowStageTrend(now.UTC(), hours, 30, samples), nil
}

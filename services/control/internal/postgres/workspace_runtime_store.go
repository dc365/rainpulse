package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workspace"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func (store *Store) LoadWorkspaceProjection(
	ctx context.Context,
	key string,
) (workspace.ProjectionRecord, error) {
	var record workspace.ProjectionRecord
	var headers json.RawMessage
	err := store.pool.QueryRow(ctx, `
SELECT projection_key, status_code, headers, body, etag,
       expires_at, stale_until, generated_at
FROM workspace_projections
WHERE projection_key = $1`, key).Scan(
		&record.Key, &record.StatusCode, &headers, &record.Body, &record.ETag,
		&record.ExpiresAt, &record.StaleUntil, &record.GeneratedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return workspace.ProjectionRecord{}, workspace.ErrProjectionNotFound
	}
	if err != nil {
		return workspace.ProjectionRecord{}, fmt.Errorf("load workspace projection: %w", err)
	}
	if err := json.Unmarshal(headers, &record.Header); err != nil {
		return workspace.ProjectionRecord{}, fmt.Errorf("decode workspace projection headers: %w", err)
	}
	return record, nil
}

func (store *Store) SaveWorkspaceProjection(
	ctx context.Context,
	record workspace.ProjectionRecord,
) error {
	headers, err := json.Marshal(record.Header)
	if err != nil {
		return fmt.Errorf("encode workspace projection headers: %w", err)
	}
	_, err = store.pool.Exec(ctx, `
INSERT INTO workspace_projections (
    projection_key, status_code, headers, body, etag,
    expires_at, stale_until, generated_at, last_accessed_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,CURRENT_TIMESTAMP)
ON CONFLICT (projection_key) DO UPDATE SET
    status_code = EXCLUDED.status_code,
    headers = EXCLUDED.headers,
    body = EXCLUDED.body,
    etag = EXCLUDED.etag,
    expires_at = EXCLUDED.expires_at,
    stale_until = EXCLUDED.stale_until,
    generated_at = EXCLUDED.generated_at,
    last_accessed_at = CURRENT_TIMESTAMP`,
		record.Key, record.StatusCode, headers, record.Body, record.ETag,
		record.ExpiresAt, record.StaleUntil, record.GeneratedAt,
	)
	if err != nil {
		return fmt.Errorf("save workspace projection: %w", err)
	}
	return nil
}

func (store *Store) DeleteExpiredWorkspaceProjections(
	ctx context.Context,
	before time.Time,
	limit int,
) error {
	if limit < 1 {
		return nil
	}
	_, err := store.pool.Exec(ctx, `
WITH expired AS (
    SELECT projection_key
    FROM workspace_projections
    WHERE stale_until < $1
    ORDER BY stale_until
    LIMIT $2
)
DELETE FROM workspace_projections AS projection
USING expired
WHERE projection.projection_key = expired.projection_key`, before, limit)
	if err != nil {
		return fmt.Errorf("delete expired workspace projections: %w", err)
	}
	return nil
}

func (store *Store) GetAnalysisDiagnosticsByJob(
	ctx context.Context,
	jobID uuid.UUID,
) (workflow.AnalysisDiagnostics, error) {
	var result workflow.AnalysisDiagnostics
	var raw json.RawMessage
	err := store.pool.QueryRow(ctx, `
SELECT job_id, bundle_uri, manifest
FROM diagnostic_runs
WHERE job_id = $1 AND status = 'SUCCEEDED'`, jobID).Scan(
		&result.JobID, &result.BundleURI, &raw,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return workflow.AnalysisDiagnostics{}, workflow.ErrNotFound
	}
	if err != nil {
		return workflow.AnalysisDiagnostics{}, fmt.Errorf("get diagnostics by job: %w", err)
	}
	if err := json.Unmarshal(raw, &result.Manifest); err != nil {
		return workflow.AnalysisDiagnostics{}, fmt.Errorf("decode diagnostics manifest: %w", err)
	}
	return result, nil
}

func (store *Store) ListCompletedNowcastNetAlgorithmRuns(
	ctx context.Context,
	limit int,
) ([]workspace.NowcastNetAlgorithmRun, error) {
	if limit < 1 || limit > 500 {
		return nil, fmt.Errorf("NowcastNet algorithm run limit is invalid")
	}
	rows, err := store.pool.Query(ctx, `
SELECT ar.algorithm_run_id, ar.run_id, ar.job_id, f.issue_time, f.grid_id,
       ar.output_uri, ar.completed_at
FROM algorithm_runs AS ar
JOIN forecast_runs AS f ON f.run_id = ar.run_id
WHERE ar.algorithm_id = 'nowcastnet' AND ar.status = 'completed'
  AND ar.output_uri IS NOT NULL AND ar.completed_at IS NOT NULL
ORDER BY ar.completed_at DESC
LIMIT $1`, limit)
	if err != nil {
		return nil, fmt.Errorf("list completed NowcastNet algorithm runs: %w", err)
	}
	defer rows.Close()
	values := make([]workspace.NowcastNetAlgorithmRun, 0)
	for rows.Next() {
		var value workspace.NowcastNetAlgorithmRun
		if err := rows.Scan(&value.AlgorithmRunID, &value.RunID, &value.JobID, &value.IssueTime,
			&value.GridID, &value.OutputURI, &value.CompletedAt); err != nil {
			return nil, fmt.Errorf("scan completed NowcastNet algorithm run: %w", err)
		}
		values = append(values, value)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate completed NowcastNet algorithm runs: %w", err)
	}
	return values, nil
}

func (store *Store) WorkspacePipelineSnapshot(
	ctx context.Context,
	gridID string,
	issueTime time.Time,
) (workspace.PipelineSnapshot, error) {
	analysis, analysisFound, err := store.workspaceAnalysis(ctx, gridID, issueTime)
	if err != nil {
		return workspace.PipelineSnapshot{}, err
	}
	run, runFound, err := store.workspaceForecastRun(ctx, gridID, issueTime)
	if err != nil {
		return workspace.PipelineSnapshot{}, err
	}
	stages := make([]workspace.PipelineStage, 0, 24)
	if analysisFound {
		full, getErr := store.GetAnalysisCycle(ctx, analysis.ID)
		if getErr != nil {
			return workspace.PipelineSnapshot{}, getErr
		}
		for _, radar := range full.Radars {
			if radar.ScanID == nil {
				continue
			}
			scan, scanErr := store.GetRadarScan(ctx, *radar.ScanID)
			if scanErr != nil {
				continue
			}
			jobs, jobsErr := store.ListJobs(ctx, scan.RunID)
			if jobsErr == nil {
				stages = append(stages, pipelineStages(jobs, radar.RadarID)...)
			}
		}
		jobs, jobsErr := store.ListJobs(ctx, analysis.RunID)
		if jobsErr == nil {
			stages = append(stages, pipelineStages(jobs, "")...)
		}
	}
	if runFound {
		jobs, jobsErr := store.ListJobs(ctx, run.ID)
		if jobsErr == nil {
			stages = append(stages, pipelineStages(jobs, "")...)
		}
	}
	stages = deduplicatePipelineStages(stages)
	sort.SliceStable(stages, func(left, right int) bool {
		leftRank := pipelineStageRank(stages[left].Stage)
		rightRank := pipelineStageRank(stages[right].Stage)
		if leftRank == rightRank {
			if stages[left].RadarID == stages[right].RadarID {
				return stages[left].StageID < stages[right].StageID
			}
			return stages[left].RadarID < stages[right].RadarID
		}
		return leftRank < rightRank
	})
	active, activeErr := store.workspaceActiveRegeneration(ctx, gridID, issueTime)
	if activeErr != nil {
		return workspace.PipelineSnapshot{}, activeErr
	}
	return workspace.PipelineSnapshot{Stages: stages, ActiveRegeneration: active}, nil
}

func (store *Store) workspaceAnalysis(
	ctx context.Context,
	gridID string,
	issueTime time.Time,
) (workflow.AnalysisCycle, bool, error) {
	cycle, err := scanAnalysis(store.pool.QueryRow(ctx, analysisSelect+`
WHERE a.grid_id = $1 AND a.analysis_time = $2 AND a.status = 'ANALYSIS_READY'
ORDER BY a.created_at DESC
LIMIT 1`, gridID, issueTime.UTC()))
	if errors.Is(err, workflow.ErrNotFound) {
		return workflow.AnalysisCycle{}, false, nil
	}
	if err != nil {
		return workflow.AnalysisCycle{}, false, err
	}
	cycle.Radars, err = store.listAnalysisRadars(ctx, cycle.ID)
	if err != nil {
		return workflow.AnalysisCycle{}, false, err
	}
	return cycle, true, nil
}

func (store *Store) workspaceForecastRun(
	ctx context.Context,
	gridID string,
	issueTime time.Time,
) (workflow.Run, bool, error) {
	run, err := scanRun(store.pool.QueryRow(ctx, runSelect+`
WHERE grid_id = $1 AND issue_time = $2
  AND status IN ('PUBLISHED', 'VERIFYING', 'VERIFIED')
ORDER BY created_at DESC
LIMIT 1`, gridID, issueTime.UTC()))
	if errors.Is(err, workflow.ErrNotFound) {
		return workflow.Run{}, false, nil
	}
	if err != nil {
		return workflow.Run{}, false, err
	}
	return run, true, nil
}

func pipelineStages(jobs []workflow.Job, radarID string) []workspace.PipelineStage {
	result := make([]workspace.PipelineStage, 0, len(jobs))
	for _, job := range jobs {
		stage, display := pipelineStageIdentity(job.JobType)
		queueMS := (*int64)(nil)
		if job.StartedAt != nil {
			value := job.StartedAt.Sub(job.CreatedAt).Milliseconds()
			if value < 0 {
				value = 0
			}
			queueMS = &value
		}
		result = append(result, workspace.PipelineStage{
			StageID: job.ID.String(), Stage: stage, DisplayName: display,
			Status: string(job.Status), RunID: &job.RunID, JobID: &job.ID,
			RadarID: radarID, ModelID: job.ModelID, ModelVersion: job.ModelVersion,
			ConfigVersion: job.ConfigVersion, QueueMS: queueMS,
			RuntimeMS: job.RuntimeMS, Attempt: job.Attempt,
			StartedAt: job.StartedAt, FinishedAt: job.FinishedAt,
			ErrorCode: stringValue(job.ErrorCode), ErrorMessage: stringValue(job.ErrorMessage),
		})
	}
	return result
}

func pipelineStageIdentity(jobType string) (string, string) {
	normalized := strings.ToLower(jobType)
	switch {
	case strings.Contains(normalized, "radar.decode"):
		return "decode", "解码"
	case strings.Contains(normalized, "radar.qc"):
		return "qc", "质控"
	case strings.Contains(normalized, "radar.grid"):
		return "grid", "格点化"
	case strings.Contains(normalized, "analysis.mosaic"):
		return "mosaic", "多雷达拼图"
	case strings.Contains(normalized, "analysis.qpe"):
		return "qpe", "降雨估算"
	case strings.Contains(normalized, "analysis.diagnostics"):
		return "diagnostics", "诊断产品"
	case strings.Contains(normalized, "nowcast.input"):
		return "nowcast_input", "模型输入"
	case strings.Contains(normalized, "pysteps_lk"):
		return "pysteps_lk", "pySTEPS-LK"
	case strings.Contains(normalized, "pysteps_steps"):
		return "pysteps_steps", "pySTEPS-STEPS"
	case strings.Contains(normalized, "nowcastnet"):
		return "nowcastnet", "NowcastNet"
	case strings.Contains(normalized, "product.build"):
		return "products", "应用产品"
	case strings.Contains(normalized, "verification"):
		return "verification", "检验"
	default:
		return normalized, jobType
	}
}

func pipelineStageRank(stage string) int {
	order := map[string]int{
		"decode": 10, "qc": 20, "grid": 30, "mosaic": 40, "qpe": 50,
		"diagnostics": 55, "nowcast_input": 60, "pysteps_lk": 70,
		"pysteps_steps": 71, "nowcastnet": 72, "products": 80, "verification": 90,
	}
	if value, ok := order[stage]; ok {
		return value
	}
	return 100
}

func deduplicatePipelineStages(values []workspace.PipelineStage) []workspace.PipelineStage {
	seen := make(map[string]struct{}, len(values))
	result := make([]workspace.PipelineStage, 0, len(values))
	for _, value := range values {
		if _, exists := seen[value.StageID]; exists {
			continue
		}
		seen[value.StageID] = struct{}{}
		result = append(result, value)
	}
	return result
}

func stringValue(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func (store *Store) workspaceActiveRegeneration(
	ctx context.Context,
	gridID string,
	issueTime time.Time,
) (*workspace.ActiveRegeneration, error) {
	var result workspace.ActiveRegeneration
	err := store.pool.QueryRow(ctx, `
SELECT request_id, target_run_id, status, reason, created_at
FROM pipeline_regeneration_requests
WHERE grid_id = $1 AND issue_time = $2
  AND status NOT IN ('SUCCEEDED', 'FAILED')
ORDER BY created_at DESC
LIMIT 1`, gridID, issueTime.UTC()).Scan(
		&result.RequestID, &result.TargetRun, &result.Status, &result.Reason, &result.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("get active workspace regeneration: %w", err)
	}
	return &result, nil
}

func (store *Store) CancelWorkspaceRegeneration(
	ctx context.Context,
	requestID uuid.UUID,
	reason string,
) (workspace.RegenerationCancellation, error) {
	tx, err := store.pool.Begin(ctx)
	if err != nil {
		return workspace.RegenerationCancellation{}, fmt.Errorf("begin regeneration cancellation: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	var targetRun uuid.UUID
	var status string
	err = tx.QueryRow(ctx, `
SELECT target_run_id, status
FROM pipeline_regeneration_requests
WHERE request_id = $1
FOR UPDATE`, requestID).Scan(&targetRun, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		return workspace.RegenerationCancellation{}, workflow.ErrNotFound
	}
	if err != nil {
		return workspace.RegenerationCancellation{}, fmt.Errorf("lock regeneration: %w", err)
	}
	if status == "SUCCEEDED" || status == "FAILED" {
		return workspace.RegenerationCancellation{}, fmt.Errorf("regeneration is already terminal with status %s", status)
	}
	cancelledAt := time.Now().UTC()
	message := "cancelled: " + reason
	if _, err = tx.Exec(ctx, `
UPDATE pipeline_regeneration_requests
SET status = 'FAILED', error_message = $2,
    cancel_reason = $3, cancelled_at = $4, updated_at = $4
WHERE request_id = $1`, requestID, message, reason, cancelledAt); err != nil {
		return workspace.RegenerationCancellation{}, fmt.Errorf("cancel regeneration request: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE jobs
SET status = 'SKIPPED', updated_at = $2
WHERE regeneration_request_id = $1 AND status IN ('PENDING', 'RUNNING')`,
		requestID, cancelledAt); err != nil {
		return workspace.RegenerationCancellation{}, fmt.Errorf("cancel regeneration jobs: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE outbox_events AS event
SET status = 'cancelled', last_error = $2
FROM jobs AS job
WHERE job.regeneration_request_id = $1
  AND event.aggregate_id = job.job_id::text
  AND event.status IN ('pending', 'failed', 'publishing')`, requestID, message); err != nil {
		return workspace.RegenerationCancellation{}, fmt.Errorf("cancel regeneration outbox events: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE forecast_runs
SET status = 'SKIPPED', reason = $2, updated_at = $3
WHERE run_id = $1 AND status NOT IN ('PUBLISHED','VERIFYING','VERIFIED','FAILED','SKIPPED')`,
		targetRun, message, cancelledAt); err != nil {
		return workspace.RegenerationCancellation{}, fmt.Errorf("cancel regeneration target run: %w", err)
	}
	if err = tx.Commit(ctx); err != nil {
		return workspace.RegenerationCancellation{}, fmt.Errorf("commit regeneration cancellation: %w", err)
	}
	return workspace.RegenerationCancellation{
		RequestID: requestID, TargetRunID: targetRun, Status: "CANCELLED",
		Reason: reason, CancelledAt: cancelledAt,
	}, nil
}

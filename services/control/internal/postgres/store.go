package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Store struct {
	pool *pgxpool.Pool
}

func New(pool *pgxpool.Pool) *Store {
	return &Store{pool: pool}
}

func (store *Store) Ping(ctx context.Context) error {
	return store.pool.Ping(ctx)
}

func (store *Store) CreateBundle(ctx context.Context, bundle workflow.CreateBundle) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin create run transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	_, err = tx.Exec(ctx, `
INSERT INTO workflow_runs (run_id, run_type, created_at)
VALUES ($1, 'forecast_run', $2)`, bundle.Run.ID, bundle.Run.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert forecast workflow identity: %w", err)
	}

	_, err = tx.Exec(ctx, `
INSERT INTO forecast_runs (
    run_id, issue_time, grid_id, config_version, status, rerun_of, reason,
    created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)`,
		bundle.Run.ID, bundle.Run.IssueTime, bundle.Run.GridID,
		bundle.Run.ConfigVersion, bundle.Run.Status, bundle.Run.RerunOf,
		"RP-003 control-plane run", bundle.Run.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert forecast run: %w", err)
	}

	_, err = tx.Exec(ctx, `
INSERT INTO jobs (
    job_id, run_id, trace_id, job_type, model_id, model_version,
    config_version, status, max_attempts, scheduled_at, created_at, updated_at,
    request_payload
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 3, $9, $9, $9, $10)`,
		bundle.Job.ID, bundle.Job.RunID, bundle.Job.TraceID, bundle.Job.JobType,
		bundle.Job.ModelID, bundle.Job.ModelVersion, bundle.Job.ConfigVersion,
		bundle.Job.Status, bundle.Job.CreatedAt, bundle.Job.RequestPayload)
	if err != nil {
		return fmt.Errorf("insert forecast job: %w", err)
	}

	_, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)`,
		bundle.Outbox.ID, bundle.Outbox.AggregateID, bundle.Outbox.EventType,
		bundle.Outbox.Subject, bundle.Outbox.Payload, bundle.Job.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert outbox event: %w", err)
	}

	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit create run transaction: %w", err)
	}
	return nil
}

func (store *Store) GetRun(ctx context.Context, runID uuid.UUID) (workflow.Run, error) {
	row := store.pool.QueryRow(ctx, runSelect+` WHERE run_id = $1`, runID)
	return scanRun(row)
}

func (store *Store) LatestRun(ctx context.Context) (workflow.Run, error) {
	row := store.pool.QueryRow(ctx, runSelect+` ORDER BY issue_time DESC, created_at DESC LIMIT 1`)
	return scanRun(row)
}

func (store *Store) ListRuns(ctx context.Context, limit int, cursor *time.Time, status *workflow.RunStatus) ([]workflow.Run, *time.Time, error) {
	var cursorValue any
	if cursor != nil {
		cursorValue = cursor.UTC()
	}
	statusValue := ""
	if status != nil {
		statusValue = string(*status)
	}
	rows, err := store.pool.Query(ctx, runSelect+`
WHERE ($1 = '' OR status = $1)
  AND ($2::timestamptz IS NULL OR created_at < $2)
ORDER BY created_at DESC
LIMIT $3`, statusValue, cursorValue, limit+1)
	if err != nil {
		return nil, nil, fmt.Errorf("list forecast runs: %w", err)
	}
	defer rows.Close()

	runs := make([]workflow.Run, 0, limit)
	for rows.Next() {
		run, err := scanRun(rows)
		if err != nil {
			return nil, nil, err
		}
		runs = append(runs, run)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("iterate forecast runs: %w", err)
	}

	var next *time.Time
	if len(runs) > limit {
		value := runs[limit-1].CreatedAt
		next = &value
		runs = runs[:limit]
	}
	return runs, next, nil
}

func (store *Store) GetJob(ctx context.Context, jobID uuid.UUID) (workflow.Job, error) {
	row := store.pool.QueryRow(ctx, jobSelect+` WHERE j.job_id = $1`, jobID)
	return scanJob(row)
}

func (store *Store) ListJobs(ctx context.Context, runID uuid.UUID) ([]workflow.Job, error) {
	rows, err := store.pool.Query(ctx, jobSelect+` WHERE j.run_id = $1 ORDER BY j.created_at`, runID)
	if err != nil {
		return nil, fmt.Errorf("list forecast jobs: %w", err)
	}
	defer rows.Close()

	jobs := make([]workflow.Job, 0)
	for rows.Next() {
		job, err := scanJob(rows)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, job)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate forecast jobs: %w", err)
	}
	return jobs, nil
}

func (store *Store) ClaimOutbox(ctx context.Context) (workflow.OutboxEvent, error) {
	row := store.pool.QueryRow(ctx, `
WITH candidate AS (
    SELECT event_id
    FROM outbox_events
    WHERE (
        status IN ('pending', 'failed')
        OR (status = 'publishing' AND available_at <= CURRENT_TIMESTAMP)
    )
      AND available_at <= CURRENT_TIMESTAMP
    ORDER BY available_at, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE outbox_events AS event
SET status = 'publishing',
    attempt_count = event.attempt_count + 1,
    available_at = CURRENT_TIMESTAMP + INTERVAL '30 seconds',
    last_error = NULL
FROM candidate
WHERE event.event_id = candidate.event_id
RETURNING event.event_id, event.aggregate_id, event.event_type, event.subject,
          event.payload, event.attempt_count`)

	var event workflow.OutboxEvent
	if err := row.Scan(&event.ID, &event.AggregateID, &event.EventType, &event.Subject, &event.Payload, &event.Attempts); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.OutboxEvent{}, workflow.ErrNotFound
		}
		return workflow.OutboxEvent{}, fmt.Errorf("claim outbox event: %w", err)
	}
	return event, nil
}

func (store *Store) MarkOutboxPublished(ctx context.Context, eventID uuid.UUID) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin publish transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var jobID uuid.UUID
	err = tx.QueryRow(ctx, `
UPDATE outbox_events
SET status = 'published', published_at = CURRENT_TIMESTAMP, last_error = NULL
WHERE event_id = $1 AND status = 'publishing'
RETURNING aggregate_id::uuid`, eventID).Scan(&jobID)
	if errors.Is(err, pgx.ErrNoRows) {
		var status string
		if lookupErr := tx.QueryRow(ctx, `SELECT status FROM outbox_events WHERE event_id = $1`, eventID).Scan(&status); lookupErr == nil && status == "published" {
			return nil
		}
	}
	if err != nil {
		return fmt.Errorf("mark outbox published: %w", err)
	}

	var runID uuid.UUID
	var jobType string
	err = tx.QueryRow(ctx, `
UPDATE jobs
SET status = CASE WHEN status = 'PENDING' THEN 'RUNNING' ELSE status END,
    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
    updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1
RETURNING run_id, job_type`, jobID).Scan(&runID, &jobType)
	if err != nil {
		return fmt.Errorf("mark published job running: %w", err)
	}
	var runType workflow.WorkflowType
	if err := tx.QueryRow(ctx, `SELECT run_type FROM workflow_runs WHERE run_id = $1`, runID).Scan(&runType); err != nil {
		return fmt.Errorf("get published workflow type: %w", err)
	}
	switch runType {
	case workflow.WorkflowForecastRun:
		if _, err := tx.Exec(ctx, `UPDATE forecast_runs SET updated_at = CURRENT_TIMESTAMP WHERE run_id = $1`, runID); err != nil {
			return fmt.Errorf("touch published forecast run: %w", err)
		}
	case workflow.WorkflowRadarScan:
		nextStatus := ""
		if jobType == orchestration.RadarDecodeJobType {
			nextStatus = "DECODING"
		} else if jobType == orchestration.RadarQCJobType {
			nextStatus = "QC_RUNNING"
		} else if jobType == orchestration.RadarGridJobType {
			nextStatus = "GRID_RUNNING"
		} else {
			return fmt.Errorf("unsupported radar job type %q", jobType)
		}
		if _, err := tx.Exec(ctx, `
UPDATE radar_scan_runs
SET status = CASE
        WHEN $2 = 'DECODING' AND status = 'RAW_VALIDATING' THEN 'DECODING'
        WHEN $2 = 'QC_RUNNING' AND status IN ('NORMALIZED', 'QC_RUNNING') THEN 'QC_RUNNING'
		WHEN $2 = 'GRID_RUNNING' AND status IN ('QC_READY', 'GRID_RUNNING') THEN 'GRID_RUNNING'
        ELSE status
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, runID, nextStatus); err != nil {
			return fmt.Errorf("mark radar scan worker running: %w", err)
		}
	case workflow.WorkflowAnalysisCycle:
		if jobType != orchestration.AnalysisMosaicJobType &&
			jobType != orchestration.AnalysisQPEJobType {
			return fmt.Errorf("unsupported analysis job type %q", jobType)
		}
		expectedStatus := workflow.AnalysisMosaic
		if jobType == orchestration.AnalysisQPEJobType {
			expectedStatus = workflow.AnalysisQPE
		}
		if _, err := tx.Exec(ctx, `
UPDATE analysis_cycles SET updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1 AND status = $2`, runID, expectedStatus); err != nil {
			return fmt.Errorf("touch published analysis cycle: %w", err)
		}
	default:
		return fmt.Errorf("unsupported published workflow type %q", runType)
	}
	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit publish transaction: %w", err)
	}
	return nil
}

func (store *Store) MarkOutboxFailed(ctx context.Context, eventID uuid.UUID, message string) error {
	_, err := store.pool.Exec(ctx, `
UPDATE outbox_events
SET status = 'failed', last_error = $2,
    available_at = CURRENT_TIMESTAMP + INTERVAL '2 seconds'
WHERE event_id = $1`, eventID, message)
	if err != nil {
		return fmt.Errorf("mark outbox failed: %w", err)
	}
	return nil
}

func (store *Store) ApplyCompletion(ctx context.Context, event orchestration.JobCompleted, raw json.RawMessage) (bool, error) {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return false, fmt.Errorf("begin completion transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	commandTag, err := tx.Exec(ctx, `
INSERT INTO inbox_events (event_id, event_type, run_id, job_id, payload)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT DO NOTHING`,
		event.EventID, event.EventType, event.RunID, event.JobID, raw)
	if err != nil {
		return false, fmt.Errorf("record inbox event: %w", err)
	}
	if commandTag.RowsAffected() == 0 {
		return false, nil
	}

	var runID, traceID uuid.UUID
	var jobType string
	var jobStatus workflow.JobStatus
	var runType workflow.WorkflowType
	err = tx.QueryRow(ctx, `
SELECT j.run_id, j.trace_id, j.job_type, j.status, wr.run_type
FROM jobs AS j
JOIN workflow_runs AS wr ON wr.run_id = j.run_id
WHERE j.job_id = $1 FOR UPDATE OF j`, event.JobID).
		Scan(&runID, &traceID, &jobType, &jobStatus, &runType)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, fmt.Errorf("%w: completion job does not exist", orchestration.ErrInvalidEvent)
	}
	if err != nil {
		return false, fmt.Errorf("lock completion job: %w", err)
	}
	if runID != event.RunID || traceID != event.TraceID {
		return false, fmt.Errorf("%w: completion trace identifiers do not match", orchestration.ErrInvalidEvent)
	}
	if !workflow.CanTransitionJob(jobStatus, workflow.JobSucceeded) {
		return false, fmt.Errorf("%w: cannot transition job from %s", orchestration.ErrInvalidEvent, jobStatus)
	}

	metadata, err := json.Marshal(map[string]any{
		"runtime_ms":  event.Payload.RuntimeMS,
		"assets":      event.Payload.Assets,
		"metrics":     event.Payload.Metrics,
		"diagnostics": event.Payload.Diagnostics,
	})
	if err != nil {
		return false, fmt.Errorf("encode completion metadata: %w", err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO job_attempts (
    job_id, attempt_no, status, started_at, completed_at, metadata
) VALUES ($1, 1, 'SUCCEEDED', $2, $3, $4)
ON CONFLICT (job_id, attempt_no) DO UPDATE
SET status = EXCLUDED.status,
    started_at = EXCLUDED.started_at,
    completed_at = EXCLUDED.completed_at,
    metadata = EXCLUDED.metadata`,
		event.JobID, event.Payload.StartedAt, event.Payload.FinishedAt, metadata)
	if err != nil {
		return false, fmt.Errorf("record completed attempt: %w", err)
	}
	_, err = tx.Exec(ctx, `
UPDATE jobs
SET status = 'SUCCEEDED', started_at = $2, completed_at = $3,
    updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1`, event.JobID, event.Payload.StartedAt, event.Payload.FinishedAt)
	if err != nil {
		return false, fmt.Errorf("complete job: %w", err)
	}

	switch runType {
	case workflow.WorkflowForecastRun:
		nextStatus := completionRunStatus(jobType)
		var currentStatus workflow.RunStatus
		if err := tx.QueryRow(ctx, `SELECT status FROM forecast_runs WHERE run_id = $1 FOR UPDATE`, runID).Scan(&currentStatus); err != nil {
			return false, fmt.Errorf("lock completion run: %w", err)
		}
		if !workflow.CanTransitionRun(currentStatus, nextStatus) {
			return false, fmt.Errorf("%w: cannot transition run from %s to %s", orchestration.ErrInvalidEvent, currentStatus, nextStatus)
		}
		_, err = tx.Exec(ctx, `
UPDATE forecast_runs SET status = $2, updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, runID, nextStatus)
		if err != nil {
			return false, fmt.Errorf("advance completed run: %w", err)
		}
	case workflow.WorkflowRadarScan:
		if err := applyRadarCompletion(ctx, tx, event, jobType); err != nil {
			return false, err
		}
	case workflow.WorkflowAnalysisCycle:
		if jobType != orchestration.AnalysisMosaicJobType &&
			jobType != orchestration.AnalysisQPEJobType {
			return false, fmt.Errorf(
				"%w: unsupported analysis job type %q",
				orchestration.ErrInvalidEvent,
				jobType,
			)
		}
		if jobType == orchestration.AnalysisMosaicJobType {
			err = applyAnalysisMosaicCompletion(ctx, tx, event)
		} else {
			err = applyAnalysisQPECompletion(ctx, tx, event)
		}
		if err != nil {
			return false, err
		}
	default:
		return false, fmt.Errorf("%w: unsupported completion workflow type %q", orchestration.ErrInvalidEvent, runType)
	}

	if err := tx.Commit(ctx); err != nil {
		return false, fmt.Errorf("commit completion transaction: %w", err)
	}
	return true, nil
}

func (store *Store) ApplyFailure(ctx context.Context, event orchestration.JobFailed, raw json.RawMessage) (bool, error) {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return false, fmt.Errorf("begin failure transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	commandTag, err := tx.Exec(ctx, `
INSERT INTO inbox_events (event_id, event_type, run_id, job_id, payload)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT DO NOTHING`,
		event.EventID, event.EventType, event.RunID, event.JobID, raw)
	if err != nil {
		return false, fmt.Errorf("record failure inbox event: %w", err)
	}
	if commandTag.RowsAffected() == 0 {
		return false, nil
	}

	var runID, traceID uuid.UUID
	var jobStatus workflow.JobStatus
	var jobType string
	var runType workflow.WorkflowType
	err = tx.QueryRow(ctx, `
SELECT j.run_id, j.trace_id, j.status, j.job_type, wr.run_type
FROM jobs AS j
JOIN workflow_runs AS wr ON wr.run_id = j.run_id
WHERE j.job_id = $1 FOR UPDATE OF j`, event.JobID).
		Scan(&runID, &traceID, &jobStatus, &jobType, &runType)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, fmt.Errorf("%w: failure job does not exist", orchestration.ErrInvalidEvent)
	}
	if err != nil {
		return false, fmt.Errorf("lock failed job: %w", err)
	}
	if runID != event.RunID || traceID != event.TraceID {
		return false, fmt.Errorf("%w: failure trace identifiers do not match", orchestration.ErrInvalidEvent)
	}
	if !workflow.CanTransitionJob(jobStatus, workflow.JobFailed) {
		return false, fmt.Errorf("%w: cannot transition job from %s", orchestration.ErrInvalidEvent, jobStatus)
	}

	metadata, err := json.Marshal(map[string]any{
		"runtime_ms": event.Payload.RuntimeMS,
		"retryable":  event.Payload.Retryable,
		"details":    event.Payload.Details,
	})
	if err != nil {
		return false, fmt.Errorf("encode failure metadata: %w", err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO job_attempts (
    job_id, attempt_no, status, error_code, error_message,
    started_at, completed_at, metadata
) VALUES ($1, 1, 'FAILED', $2, $3, $4, $5, $6)
ON CONFLICT (job_id, attempt_no) DO UPDATE
SET status = EXCLUDED.status,
    error_code = EXCLUDED.error_code,
    error_message = EXCLUDED.error_message,
    started_at = EXCLUDED.started_at,
    completed_at = EXCLUDED.completed_at,
    metadata = EXCLUDED.metadata`,
		event.JobID, event.Payload.ErrorCode, event.Payload.ErrorMessage,
		event.Payload.StartedAt, event.Payload.FinishedAt, metadata)
	if err != nil {
		return false, fmt.Errorf("record failed attempt: %w", err)
	}
	_, err = tx.Exec(ctx, `
UPDATE jobs
SET status = 'FAILED', started_at = $2, completed_at = $3,
    updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1`, event.JobID, event.Payload.StartedAt, event.Payload.FinishedAt)
	if err != nil {
		return false, fmt.Errorf("fail job: %w", err)
	}

	switch runType {
	case workflow.WorkflowForecastRun:
		var currentStatus workflow.RunStatus
		if err := tx.QueryRow(ctx, `SELECT status FROM forecast_runs WHERE run_id = $1 FOR UPDATE`, runID).Scan(&currentStatus); err != nil {
			return false, fmt.Errorf("lock failed run: %w", err)
		}
		if !workflow.CanTransitionRun(currentStatus, workflow.RunFailed) {
			return false, fmt.Errorf("%w: cannot transition run from %s to FAILED", orchestration.ErrInvalidEvent, currentStatus)
		}
		_, err = tx.Exec(ctx, `
UPDATE forecast_runs
SET status = 'FAILED', reason = $2, updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, runID, event.Payload.ErrorCode)
		if err != nil {
			return false, fmt.Errorf("fail run: %w", err)
		}
	case workflow.WorkflowRadarScan:
		_, err = tx.Exec(ctx, `
UPDATE radar_scan_runs
SET status = 'FAILED', degraded_reason = $2, updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, runID, event.Payload.ErrorCode)
		if err != nil {
			return false, fmt.Errorf("fail radar scan run: %w", err)
		}
	case workflow.WorkflowAnalysisCycle:
		_, err = tx.Exec(ctx, `
UPDATE analysis_cycles
SET status = 'FAILED', degraded_reason = $2, updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, runID, event.Payload.ErrorCode)
		if err != nil {
			return false, fmt.Errorf("fail analysis cycle: %w", err)
		}
		if jobType == orchestration.AnalysisMosaicJobType {
			_, err = tx.Exec(ctx, `
UPDATE mosaic_runs
SET status = 'FAILED', updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1`, event.JobID)
			if err != nil {
				return false, fmt.Errorf("fail mosaic run: %w", err)
			}
		} else if jobType == orchestration.AnalysisQPEJobType {
			_, err = tx.Exec(ctx, `
UPDATE qpe_runs
SET status = 'FAILED', updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1`, event.JobID)
			if err != nil {
				return false, fmt.Errorf("fail QPE run: %w", err)
			}
		} else {
			return false, fmt.Errorf("%w: unsupported analysis job type %q", orchestration.ErrInvalidEvent, jobType)
		}
	default:
		return false, fmt.Errorf("%w: unsupported failure workflow type %q", orchestration.ErrInvalidEvent, runType)
	}

	if err := tx.Commit(ctx); err != nil {
		return false, fmt.Errorf("commit failure transaction: %w", err)
	}
	return true, nil
}

func applyRadarCompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
	jobType string,
) error {
	switch jobType {
	case orchestration.RadarDecodeJobType:
		return applyRadarDecodeCompletion(ctx, tx, event)
	case orchestration.RadarQCJobType:
		return applyRadarQCCompletion(ctx, tx, event)
	case orchestration.RadarGridJobType:
		return applyRadarGridCompletion(ctx, tx, event)
	default:
		return fmt.Errorf("%w: unsupported radar job type %q", orchestration.ErrInvalidEvent, jobType)
	}
}

func applyRadarDecodeCompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
) error {
	var normalized *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		if event.Payload.Assets[index].AssetType == "normalized_radar_volume" {
			if normalized != nil {
				return fmt.Errorf("%w: multiple normalized radar assets", orchestration.ErrInvalidEvent)
			}
			normalized = &event.Payload.Assets[index]
		}
	}
	if normalized == nil {
		return fmt.Errorf("%w: normalized radar asset is required", orchestration.ErrInvalidEvent)
	}
	rawHealth, ok := event.Payload.Diagnostics["radar_health"]
	if !ok {
		return fmt.Errorf("%w: radar health diagnostics are required", orchestration.ErrInvalidEvent)
	}
	var health workflow.RadarHealthMetrics
	if err := json.Unmarshal(rawHealth, &health); err != nil {
		return fmt.Errorf("%w: decode radar health diagnostics: %v", orchestration.ErrInvalidEvent, err)
	}
	if health.Health != workflow.RadarHealthHealthy && health.Health != workflow.RadarHealthDegraded &&
		health.Health != workflow.RadarHealthUnavailable {
		return fmt.Errorf("%w: invalid radar health state %q", orchestration.ErrInvalidEvent, health.Health)
	}
	if health.ScanCompleteness < 0 || health.ScanCompleteness > 1 || health.ExpectedSweepCount <= 0 ||
		health.ExpectedRadialCount <= 0 || health.MaximumAzimuthGapDeg < 0 || health.MaximumAzimuthGapDeg > 360 {
		return fmt.Errorf("%w: invalid radar health metrics", orchestration.ErrInvalidEvent)
	}

	var scanID uuid.UUID
	var radarID, configVersion string
	if err := tx.QueryRow(ctx, `
SELECT scan_id, radar_id, radar_config_version
FROM radar_scan_runs WHERE run_id = $1 FOR UPDATE`, event.RunID).
		Scan(&scanID, &radarID, &configVersion); err != nil {
		return fmt.Errorf("lock radar scan completion: %w", err)
	}
	if health.RadarID != radarID || health.RadarConfigVersion != configVersion {
		return fmt.Errorf("%w: radar health identity does not match scan", orchestration.ErrInvalidEvent)
	}
	health.ScanID = scanID
	health.MeasuredAt = event.Payload.FinishedAt
	fields, err := json.Marshal(health.FieldAvailability)
	if err != nil {
		return fmt.Errorf("encode field availability: %w", err)
	}
	noise, err := json.Marshal(health.NoiseLevel)
	if err != nil {
		return fmt.Errorf("encode noise telemetry: %w", err)
	}
	diagnostics, err := json.Marshal(health)
	if err != nil {
		return fmt.Errorf("encode radar health diagnostics: %w", err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO radar_health_metrics (
    scan_id, radar_id, health_profile_version, health_state, scan_completeness,
    expected_sweep_count, actual_sweep_count, missing_sweep_numbers,
    expected_radial_count, actual_radial_count, missing_radial_count,
    maximum_azimuth_gap_deg, field_availability, noise_level, channel_status,
    anomaly_count, diagnostics, measured_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18
)
ON CONFLICT (scan_id) DO UPDATE SET
    health_profile_version = EXCLUDED.health_profile_version,
    health_state = EXCLUDED.health_state,
    scan_completeness = EXCLUDED.scan_completeness,
    expected_sweep_count = EXCLUDED.expected_sweep_count,
    actual_sweep_count = EXCLUDED.actual_sweep_count,
    missing_sweep_numbers = EXCLUDED.missing_sweep_numbers,
    expected_radial_count = EXCLUDED.expected_radial_count,
    actual_radial_count = EXCLUDED.actual_radial_count,
    missing_radial_count = EXCLUDED.missing_radial_count,
    maximum_azimuth_gap_deg = EXCLUDED.maximum_azimuth_gap_deg,
    field_availability = EXCLUDED.field_availability,
    noise_level = EXCLUDED.noise_level,
    channel_status = EXCLUDED.channel_status,
    anomaly_count = EXCLUDED.anomaly_count,
    diagnostics = EXCLUDED.diagnostics,
    measured_at = EXCLUDED.measured_at`,
		scanID, radarID, health.HealthProfileVersion, health.Health,
		health.ScanCompleteness, health.ExpectedSweepCount, health.ActualSweepCount,
		health.MissingSweepNumbers, health.ExpectedRadialCount, health.ActualRadialCount,
		health.MissingRadialCount, health.MaximumAzimuthGapDeg, fields, noise,
		health.ChannelStatus, health.AnomalyCount, diagnostics, health.MeasuredAt)
	if err != nil {
		return fmt.Errorf("persist radar health metrics: %w", err)
	}
	_, err = tx.Exec(ctx, `
UPDATE radar_scan_runs
SET status = 'NORMALIZED', normalized_uri = $2, scan_completeness = $3,
    degraded_reason = CASE WHEN $4 = 'HEALTHY' THEN NULL ELSE array_to_string($5::text[], ',') END,
    updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, event.RunID, normalized.URI, health.ScanCompleteness,
		health.Health, health.HealthReasons)
	if err != nil {
		return fmt.Errorf("complete radar scan run: %w", err)
	}
	qualityState := "valid"
	if health.Health != workflow.RadarHealthHealthy {
		qualityState = "low_quality"
	}
	_, err = tx.Exec(ctx, `
UPDATE input_assets AS asset
SET quality_state = $2
FROM radar_scans AS scan
WHERE scan.scan_id = $1 AND asset.asset_id = scan.raw_asset_id`, scanID, qualityState)
	if err != nil {
		return fmt.Errorf("update raw radar asset quality state: %w", err)
	}
	return nil
}

func applyRadarQCCompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
) error {
	var qcAsset *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		if event.Payload.Assets[index].AssetType == "qc_radar_volume" {
			if qcAsset != nil {
				return fmt.Errorf("%w: multiple QC radar assets", orchestration.ErrInvalidEvent)
			}
			qcAsset = &event.Payload.Assets[index]
		}
	}
	if qcAsset == nil {
		return fmt.Errorf("%w: QC radar asset is required", orchestration.ErrInvalidEvent)
	}
	rawQC, ok := event.Payload.Diagnostics["radar_qc"]
	if !ok {
		return fmt.Errorf("%w: radar QC diagnostics are required", orchestration.ErrInvalidEvent)
	}
	var metrics workflow.RadarQCMetrics
	if err := json.Unmarshal(rawQC, &metrics); err != nil {
		return fmt.Errorf("%w: decode radar QC diagnostics: %v", orchestration.ErrInvalidEvent, err)
	}
	if metrics.MeanQualityIndex < 0 || metrics.MeanQualityIndex > 1 ||
		metrics.ValidGateCount < 0 || metrics.MissingGateCount < 0 ||
		metrics.LowQualityGateCount < 0 || metrics.LowQualityGateCount > metrics.ValidGateCount ||
		metrics.NoRainGateCount < 0 || metrics.RadialInterferenceRayCount < 0 ||
		metrics.GroundClutterGateCount < 0 || metrics.SeaClutterGateCount < 0 ||
		metrics.APGateCount < 0 || metrics.QCProfile == "" || metrics.QCPipelineVersion == "" ||
		metrics.FlagDefinitionVersion == "" {
		return fmt.Errorf("%w: invalid radar QC metrics", orchestration.ErrInvalidEvent)
	}
	if metrics.HealthState != workflow.RadarHealthHealthy &&
		metrics.HealthState != workflow.RadarHealthDegraded {
		return fmt.Errorf("%w: invalid QC health state %q", orchestration.ErrInvalidEvent, metrics.HealthState)
	}
	var scanID uuid.UUID
	var radarID string
	var status workflow.RadarScanStatus
	if err := tx.QueryRow(ctx, `
SELECT scan_id, radar_id, status FROM radar_scan_runs
WHERE run_id = $1 FOR UPDATE`, event.RunID).Scan(&scanID, &radarID, &status); err != nil {
		return fmt.Errorf("lock radar QC completion: %w", err)
	}
	if status != workflow.RadarScanQCRunning && status != workflow.RadarScanQCReady {
		return fmt.Errorf("%w: radar scan status %q cannot complete QC", orchestration.ErrInvalidEvent, status)
	}
	if metrics.ScanID != scanID || metrics.RadarID != radarID {
		return fmt.Errorf("%w: radar QC identity does not match scan", orchestration.ErrInvalidEvent)
	}
	metrics.MeasuredAt = event.Payload.FinishedAt
	moduleStatuses, err := json.Marshal(metrics.ModuleStatuses)
	if err != nil {
		return fmt.Errorf("encode radar QC module statuses: %w", err)
	}
	diagnostics, err := json.Marshal(metrics)
	if err != nil {
		return fmt.Errorf("encode radar QC diagnostics: %w", err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO radar_qc_metrics (
    scan_id, radar_id, qc_profile, qc_pipeline_version,
    flag_definition_version, health_state, mean_quality_index,
    valid_gate_count, missing_gate_count, low_quality_gate_count,
    no_rain_gate_count, radial_interference_ray_count,
    ground_clutter_gate_count, sea_clutter_gate_count, ap_gate_count,
    module_statuses, diagnostics, measured_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18
)
ON CONFLICT (scan_id) DO UPDATE SET
    qc_profile = EXCLUDED.qc_profile,
    qc_pipeline_version = EXCLUDED.qc_pipeline_version,
    flag_definition_version = EXCLUDED.flag_definition_version,
    health_state = EXCLUDED.health_state,
    mean_quality_index = EXCLUDED.mean_quality_index,
    valid_gate_count = EXCLUDED.valid_gate_count,
    missing_gate_count = EXCLUDED.missing_gate_count,
    low_quality_gate_count = EXCLUDED.low_quality_gate_count,
    no_rain_gate_count = EXCLUDED.no_rain_gate_count,
    radial_interference_ray_count = EXCLUDED.radial_interference_ray_count,
    ground_clutter_gate_count = EXCLUDED.ground_clutter_gate_count,
    sea_clutter_gate_count = EXCLUDED.sea_clutter_gate_count,
    ap_gate_count = EXCLUDED.ap_gate_count,
    module_statuses = EXCLUDED.module_statuses,
    diagnostics = EXCLUDED.diagnostics,
    measured_at = EXCLUDED.measured_at`,
		scanID, radarID, metrics.QCProfile, metrics.QCPipelineVersion,
		metrics.FlagDefinitionVersion, metrics.HealthState, metrics.MeanQualityIndex,
		metrics.ValidGateCount, metrics.MissingGateCount, metrics.LowQualityGateCount,
		metrics.NoRainGateCount, metrics.RadialInterferenceRayCount,
		metrics.GroundClutterGateCount, metrics.SeaClutterGateCount, metrics.APGateCount,
		moduleStatuses, diagnostics, metrics.MeasuredAt)
	if err != nil {
		return fmt.Errorf("persist radar QC metrics: %w", err)
	}
	_, err = tx.Exec(ctx, `
UPDATE radar_scan_runs
SET status = 'QC_READY', qc_uri = $2, mean_quality_index = $3,
    degraded_reason = CASE
        WHEN $4 = 'HEALTHY' THEN NULL
        ELSE COALESCE((
            SELECT NULLIF(array_to_string(ARRAY(
                SELECT jsonb_array_elements_text(health.diagnostics -> 'health_reasons')
            ), ','), '')
            FROM radar_health_metrics AS health
            WHERE health.scan_id = radar_scan_runs.scan_id
        ), 'RADAR_HEALTH_DEGRADED')
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, event.RunID, qcAsset.URI, metrics.MeanQualityIndex, metrics.HealthState)
	if err != nil {
		return fmt.Errorf("complete radar QC run: %w", err)
	}
	return nil
}

func applyRadarGridCompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
) error {
	var gridAsset *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		if event.Payload.Assets[index].AssetType == "radar_grid" {
			if gridAsset != nil {
				return fmt.Errorf("%w: multiple radar grid assets", orchestration.ErrInvalidEvent)
			}
			gridAsset = &event.Payload.Assets[index]
		}
	}
	if gridAsset == nil {
		return fmt.Errorf("%w: radar grid asset is required", orchestration.ErrInvalidEvent)
	}
	rawGrid, ok := event.Payload.Diagnostics["radar_grid"]
	if !ok {
		return fmt.Errorf("%w: radar grid diagnostics are required", orchestration.ErrInvalidEvent)
	}
	var metrics workflow.RadarGridMetrics
	if err := json.Unmarshal(rawGrid, &metrics); err != nil {
		return fmt.Errorf("%w: decode radar grid diagnostics: %v", orchestration.ErrInvalidEvent, err)
	}
	if metrics.RadarID == "" || metrics.GridID == "" || metrics.GridConfigVersion == "" ||
		metrics.ProfileVersion == "" || metrics.AlgorithmVersion == "" ||
		metrics.DEMAssetVersion == "" || metrics.VerticalDatumStatus == "" ||
		metrics.GridCellCount <= 0 || metrics.ValidCellCount < 0 ||
		metrics.MissingCellCount < 0 || metrics.LowQualityCellCount < 0 ||
		metrics.ValidCellCount+metrics.MissingCellCount != metrics.GridCellCount ||
		metrics.LowQualityCellCount > metrics.ValidCellCount ||
		metrics.ValidCoverageRatio < 0 || metrics.ValidCoverageRatio > 1 ||
		metrics.MeanQualityIndex < 0 || metrics.MeanQualityIndex > 1 ||
		metrics.BeamBlockedMissingCellCount < 0 {
		return fmt.Errorf("%w: invalid radar grid metrics", orchestration.ErrInvalidEvent)
	}
	if metrics.OperationalEligible && len(metrics.OperationalReasons) != 0 {
		return fmt.Errorf("%w: eligible radar grid has operational blockers", orchestration.ErrInvalidEvent)
	}
	var scanID uuid.UUID
	var radarID string
	var status workflow.RadarScanStatus
	if err := tx.QueryRow(ctx, `
SELECT scan_id, radar_id, status FROM radar_scan_runs
WHERE run_id = $1 FOR UPDATE`, event.RunID).Scan(&scanID, &radarID, &status); err != nil {
		return fmt.Errorf("lock radar grid completion: %w", err)
	}
	if status != workflow.RadarScanGridRunning && status != workflow.RadarScanGridReady {
		return fmt.Errorf(
			"%w: radar scan status %q cannot complete gridding",
			orchestration.ErrInvalidEvent,
			status,
		)
	}
	if metrics.ScanID != scanID || metrics.RadarID != radarID {
		return fmt.Errorf("%w: radar grid identity does not match scan", orchestration.ErrInvalidEvent)
	}
	metrics.MeasuredAt = event.Payload.FinishedAt
	selectionCounts, err := json.Marshal(metrics.SelectionCounts)
	if err != nil {
		return fmt.Errorf("encode radar grid selection counts: %w", err)
	}
	diagnostics, err := json.Marshal(metrics)
	if err != nil {
		return fmt.Errorf("encode radar grid diagnostics: %w", err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO radar_grid_metrics (
    scan_id, radar_id, grid_id, grid_config_version, profile_version,
    algorithm_version, dem_asset_version, vertical_datum_status,
    operational_eligible, operational_reasons, grid_cell_count,
    valid_cell_count, missing_cell_count, low_quality_cell_count,
    valid_coverage_ratio, mean_quality_index, beam_blocked_missing_cell_count,
    selection_counts, diagnostics, measured_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
    $17, $18, $19, $20
)
ON CONFLICT (scan_id) DO UPDATE SET
    grid_id = EXCLUDED.grid_id,
    grid_config_version = EXCLUDED.grid_config_version,
    profile_version = EXCLUDED.profile_version,
    algorithm_version = EXCLUDED.algorithm_version,
    dem_asset_version = EXCLUDED.dem_asset_version,
    vertical_datum_status = EXCLUDED.vertical_datum_status,
    operational_eligible = EXCLUDED.operational_eligible,
    operational_reasons = EXCLUDED.operational_reasons,
    grid_cell_count = EXCLUDED.grid_cell_count,
    valid_cell_count = EXCLUDED.valid_cell_count,
    missing_cell_count = EXCLUDED.missing_cell_count,
    low_quality_cell_count = EXCLUDED.low_quality_cell_count,
    valid_coverage_ratio = EXCLUDED.valid_coverage_ratio,
    mean_quality_index = EXCLUDED.mean_quality_index,
    beam_blocked_missing_cell_count = EXCLUDED.beam_blocked_missing_cell_count,
    selection_counts = EXCLUDED.selection_counts,
    diagnostics = EXCLUDED.diagnostics,
    measured_at = EXCLUDED.measured_at`,
		scanID, radarID, metrics.GridID, metrics.GridConfigVersion,
		metrics.ProfileVersion, metrics.AlgorithmVersion, metrics.DEMAssetVersion,
		metrics.VerticalDatumStatus, metrics.OperationalEligible,
		metrics.OperationalReasons, metrics.GridCellCount, metrics.ValidCellCount,
		metrics.MissingCellCount, metrics.LowQualityCellCount,
		metrics.ValidCoverageRatio, metrics.MeanQualityIndex,
		metrics.BeamBlockedMissingCellCount, selectionCounts, diagnostics,
		metrics.MeasuredAt)
	if err != nil {
		return fmt.Errorf("persist radar grid metrics: %w", err)
	}
	_, err = tx.Exec(ctx, `
UPDATE radar_scan_runs
SET status = 'RADAR_GRID_READY', grid_uri = $2, mean_quality_index = $3,
    degraded_reason = CASE
        WHEN $4 THEN degraded_reason
        ELSE NULLIF(concat_ws(',', degraded_reason, array_to_string($5::text[], ',')), '')
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, event.RunID, gridAsset.URI, metrics.MeanQualityIndex,
		metrics.OperationalEligible, metrics.OperationalReasons)
	if err != nil {
		return fmt.Errorf("complete radar grid run: %w", err)
	}
	return nil
}

func applyAnalysisMosaicCompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
) error {
	var mosaicAsset *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		if event.Payload.Assets[index].AssetType == "radar_mosaic" {
			if mosaicAsset != nil {
				return fmt.Errorf("%w: multiple radar mosaic assets", orchestration.ErrInvalidEvent)
			}
			mosaicAsset = &event.Payload.Assets[index]
		}
	}
	if mosaicAsset == nil {
		return fmt.Errorf("%w: radar mosaic asset is required", orchestration.ErrInvalidEvent)
	}
	rawMosaic, ok := event.Payload.Diagnostics["radar_mosaic"]
	if !ok {
		return fmt.Errorf("%w: radar mosaic diagnostics are required", orchestration.ErrInvalidEvent)
	}
	var metrics workflow.AnalysisMosaicMetrics
	if err := json.Unmarshal(rawMosaic, &metrics); err != nil {
		return fmt.Errorf("%w: decode radar mosaic diagnostics: %v", orchestration.ErrInvalidEvent, err)
	}
	if metrics.AnalysisTime.IsZero() || metrics.GridID == "" ||
		metrics.GridConfigVersion == "" || metrics.ProfileVersion == "" ||
		metrics.AlgorithmVersion == "" || metrics.InputRadarCount <= 0 ||
		metrics.ActualContributingRadarCount <= 0 ||
		metrics.ActualContributingRadarCount > metrics.InputRadarCount ||
		metrics.GridCellCount <= 0 || metrics.ValidCellCount < 0 ||
		metrics.MissingCellCount < 0 || metrics.LowQualityCellCount < 0 ||
		metrics.BlendedCellCount < 0 ||
		metrics.ValidCellCount+metrics.MissingCellCount != metrics.GridCellCount ||
		metrics.LowQualityCellCount > metrics.ValidCellCount ||
		metrics.BlendedCellCount > metrics.ValidCellCount ||
		metrics.ValidCoverageRatio < 0 || metrics.ValidCoverageRatio > 1 ||
		metrics.MeanQualityIndex < 0 || metrics.MeanQualityIndex > 1 ||
		len(metrics.Contributors) != metrics.InputRadarCount {
		return fmt.Errorf("%w: invalid radar mosaic metrics", orchestration.ErrInvalidEvent)
	}
	if metrics.OperationalEligible != (len(metrics.OperationalReasons) == 0) {
		return fmt.Errorf("%w: mosaic operational eligibility is inconsistent", orchestration.ErrInvalidEvent)
	}
	seenRadars := make(map[string]struct{}, len(metrics.Contributors))
	seenScans := make(map[uuid.UUID]struct{}, len(metrics.Contributors))
	actualCount := 0
	for _, contributor := range metrics.Contributors {
		if contributor.RadarID == "" || contributor.ScanID == uuid.Nil ||
			contributor.GridURI == "" || contributor.HybridScanVersion == "" ||
			contributor.ContributingCellCount < 0 ||
			contributor.MeanAdjustedQualityIndex < 0 ||
			contributor.MeanAdjustedQualityIndex > 1 {
			return fmt.Errorf("%w: invalid mosaic contributor metrics", orchestration.ErrInvalidEvent)
		}
		if _, exists := seenRadars[contributor.RadarID]; exists {
			return fmt.Errorf("%w: duplicate mosaic contributor radar", orchestration.ErrInvalidEvent)
		}
		if _, exists := seenScans[contributor.ScanID]; exists {
			return fmt.Errorf("%w: duplicate mosaic contributor scan", orchestration.ErrInvalidEvent)
		}
		seenRadars[contributor.RadarID] = struct{}{}
		seenScans[contributor.ScanID] = struct{}{}
		if contributor.ContributingCellCount > 0 {
			actualCount++
		}
	}
	if actualCount != metrics.ActualContributingRadarCount {
		return fmt.Errorf("%w: actual mosaic contributor count is inconsistent", orchestration.ErrInvalidEvent)
	}

	var analysisID uuid.UUID
	var status workflow.AnalysisStatus
	var analysisTime time.Time
	var gridID, configVersion, algorithmVersion string
	if err := tx.QueryRow(ctx, `
SELECT a.analysis_id, a.status, a.analysis_time, a.grid_id, a.config_version,
       m.mosaic_algorithm_version
FROM analysis_cycles AS a
JOIN mosaic_runs AS m ON m.analysis_id = a.analysis_id
WHERE a.run_id = $1 FOR UPDATE OF a, m`, event.RunID).
		Scan(&analysisID, &status, &analysisTime, &gridID, &configVersion,
			&algorithmVersion); err != nil {
		return fmt.Errorf("lock analysis mosaic completion: %w", err)
	}
	if status != workflow.AnalysisMosaic && status != workflow.AnalysisQPE {
		return fmt.Errorf(
			"%w: analysis status %q cannot complete mosaic",
			orchestration.ErrInvalidEvent,
			status,
		)
	}
	if !metrics.AnalysisTime.Equal(analysisTime) || metrics.GridID != gridID ||
		metrics.ProfileVersion != configVersion || metrics.AlgorithmVersion != algorithmVersion {
		return fmt.Errorf("%w: radar mosaic identity does not match analysis", orchestration.ErrInvalidEvent)
	}
	metrics.MeasuredAt = event.Payload.FinishedAt
	diagnostics, err := json.Marshal(metrics)
	if err != nil {
		return fmt.Errorf("encode radar mosaic diagnostics: %w", err)
	}
	for _, contributor := range metrics.Contributors {
		result, err := tx.Exec(ctx, `
UPDATE analysis_cycle_radars
SET mean_quality_index = $4
WHERE analysis_id = $1 AND radar_id = $2 AND scan_id = $3
  AND state = 'PARTICIPATING'`,
			analysisID, contributor.RadarID, contributor.ScanID,
			contributor.MeanAdjustedQualityIndex)
		if err != nil {
			return fmt.Errorf("update mosaic contributor %s: %w", contributor.RadarID, err)
		}
		if result.RowsAffected() != 1 {
			return fmt.Errorf(
				"%w: mosaic contributor does not match aligned analysis radar",
				orchestration.ErrInvalidEvent,
			)
		}
	}
	_, err = tx.Exec(ctx, `
UPDATE mosaic_runs
SET status = 'SUCCEEDED', mosaic_uri = $2, diagnostics = $3,
    measured_at = $4, updated_at = CURRENT_TIMESTAMP
WHERE analysis_id = $1`, analysisID, mosaicAsset.URI, diagnostics, metrics.MeasuredAt)
	if err != nil {
		return fmt.Errorf("persist mosaic run completion: %w", err)
	}
	var degradedReason *string
	if !metrics.OperationalEligible {
		reason := strings.Join(metrics.OperationalReasons, ",")
		degradedReason = &reason
	}
	_, err = tx.Exec(ctx, `
UPDATE analysis_cycles
SET status = 'QPE_RUNNING', mosaic_uri = $2, radar_count = $3,
    valid_coverage_ratio = $4, mean_quality_index = $5,
    degraded_reason = $6, updated_at = CURRENT_TIMESTAMP
WHERE analysis_id = $1`, analysisID, mosaicAsset.URI,
		metrics.ActualContributingRadarCount, metrics.ValidCoverageRatio,
		metrics.MeanQualityIndex, degradedReason)
	if err != nil {
		return fmt.Errorf("advance mosaic to QPE: %w", err)
	}
	return nil
}

func applyAnalysisQPECompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
) error {
	var analysisAsset *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		if event.Payload.Assets[index].AssetType == "radar_analysis" {
			if analysisAsset != nil {
				return fmt.Errorf("%w: multiple radar analysis assets", orchestration.ErrInvalidEvent)
			}
			analysisAsset = &event.Payload.Assets[index]
		}
	}
	if analysisAsset == nil {
		return fmt.Errorf("%w: radar analysis asset is required", orchestration.ErrInvalidEvent)
	}
	rawQPE, ok := event.Payload.Diagnostics["analysis_qpe"]
	if !ok {
		return fmt.Errorf("%w: analysis QPE diagnostics are required", orchestration.ErrInvalidEvent)
	}
	var metrics workflow.AnalysisQPEMetrics
	if err := json.Unmarshal(rawQPE, &metrics); err != nil {
		return fmt.Errorf("%w: decode analysis QPE diagnostics: %v", orchestration.ErrInvalidEvent, err)
	}
	if metrics.AnalysisID == uuid.Nil || metrics.AnalysisTime.IsZero() || metrics.GridID == "" ||
		metrics.GridConfigVersion == "" || metrics.QPEConfigVersion == "" ||
		metrics.QPEAlgorithmVersion == "" || metrics.MosaicConfigVersion == "" ||
		metrics.MosaicAlgorithmVersion == "" || metrics.FlagDefinitionVersion == "" ||
		metrics.InputMosaicURI == "" ||
		metrics.InputField != "DBZH_QC" || metrics.CoefficientA <= 0 ||
		metrics.ExponentB <= 0 || metrics.MaximumRateMMH <= 0 ||
		metrics.GaugeAdjustmentEnabled || metrics.GridCellCount <= 0 ||
		metrics.ValidCellCount < 0 || metrics.MissingCellCount < 0 ||
		metrics.LowQualityCellCount < 0 || metrics.NoRainCellCount < 0 ||
		metrics.RainCellCount < 0 || metrics.CappedCellCount < 0 ||
		metrics.ValidCellCount+metrics.MissingCellCount != metrics.GridCellCount ||
		metrics.NoRainCellCount+metrics.RainCellCount != metrics.ValidCellCount ||
		metrics.LowQualityCellCount > metrics.ValidCellCount ||
		metrics.CappedCellCount > metrics.RainCellCount ||
		metrics.ValidCoverageRatio < 0 || metrics.ValidCoverageRatio > 1 ||
		metrics.MeanQualityIndex < 0 || metrics.MeanQualityIndex > 1 ||
		metrics.MeanRateMMH < 0 || metrics.MaximumObservedRateMMH < 0 ||
		metrics.UncappedMaximumRateMMH+1e-6 < metrics.MaximumObservedRateMMH ||
		metrics.P95RateMMH < 0 || metrics.MaximumObservedRateMMH > metrics.MaximumRateMMH {
		return fmt.Errorf("%w: invalid analysis QPE metrics", orchestration.ErrInvalidEvent)
	}
	if metrics.OperationalEligible != (len(metrics.OperationalReasons) == 0) {
		return fmt.Errorf("%w: QPE operational eligibility is inconsistent", orchestration.ErrInvalidEvent)
	}

	var analysisID uuid.UUID
	var status workflow.AnalysisStatus
	var analysisTime time.Time
	var gridID, qpeConfigVersion, qpeAlgorithmVersion, inputMosaicURI string
	var rawRequest json.RawMessage
	if err := tx.QueryRow(ctx, `
SELECT a.analysis_id, a.status, a.analysis_time, a.grid_id,
       q.qpe_config_version, q.qpe_algorithm_version, q.input_mosaic_uri,
       j.request_payload
FROM analysis_cycles AS a
JOIN qpe_runs AS q ON q.analysis_id = a.analysis_id
JOIN jobs AS j ON j.job_id = q.job_id
WHERE a.run_id = $1 AND q.job_id = $2 FOR UPDATE OF a, q`,
		event.RunID, event.JobID).Scan(
		&analysisID, &status, &analysisTime, &gridID,
		&qpeConfigVersion, &qpeAlgorithmVersion, &inputMosaicURI, &rawRequest,
	); err != nil {
		return fmt.Errorf("lock analysis QPE completion: %w", err)
	}
	if status != workflow.AnalysisQPE {
		return fmt.Errorf(
			"%w: analysis status %q cannot complete QPE",
			orchestration.ErrInvalidEvent,
			status,
		)
	}
	var requested orchestration.AnalysisQPERequested
	if err := json.Unmarshal(rawRequest, &requested); err != nil {
		return fmt.Errorf("decode stored analysis QPE request: %w", err)
	}
	if metrics.AnalysisID != analysisID || requested.Payload.AnalysisID != analysisID ||
		!metrics.AnalysisTime.Equal(analysisTime) ||
		!requested.Payload.AnalysisTime.Equal(analysisTime) ||
		metrics.GridID != gridID || requested.Payload.GridID != gridID ||
		metrics.QPEConfigVersion != qpeConfigVersion ||
		requested.Payload.QPEConfigVersion != qpeConfigVersion ||
		metrics.QPEAlgorithmVersion != qpeAlgorithmVersion ||
		requested.Payload.QPEAlgorithmVersion != qpeAlgorithmVersion ||
		metrics.InputMosaicURI != inputMosaicURI ||
		requested.Payload.InputURI != inputMosaicURI ||
		metrics.GridConfigVersion != requested.Payload.GridConfigVersion ||
		metrics.MosaicConfigVersion != requested.Payload.MosaicConfigVersion ||
		metrics.MosaicAlgorithmVersion != requested.Payload.MosaicAlgorithm ||
		metrics.FlagDefinitionVersion != requested.Payload.FlagDefinitionVersion {
		return fmt.Errorf("%w: QPE identity does not match analysis", orchestration.ErrInvalidEvent)
	}
	metrics.MeasuredAt = event.Payload.FinishedAt
	diagnostics, err := json.Marshal(metrics)
	if err != nil {
		return fmt.Errorf("encode analysis QPE diagnostics: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE qpe_runs
SET status = 'SUCCEEDED', analysis_uri = $2, diagnostics = $3,
    measured_at = $4, updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1`, event.JobID, analysisAsset.URI, diagnostics,
		metrics.MeasuredAt); err != nil {
		return fmt.Errorf("persist QPE run completion: %w", err)
	}
	var degradedReason *string
	if !metrics.OperationalEligible {
		reason := strings.Join(metrics.OperationalReasons, ",")
		degradedReason = &reason
	}
	if _, err = tx.Exec(ctx, `
UPDATE analysis_cycles
SET status = 'ANALYSIS_READY', analysis_uri = $2,
    valid_coverage_ratio = $3, mean_quality_index = $4,
    degraded_reason = $5, updated_at = CURRENT_TIMESTAMP
WHERE analysis_id = $1`, analysisID, analysisAsset.URI,
		metrics.ValidCoverageRatio, metrics.MeanQualityIndex,
		degradedReason); err != nil {
		return fmt.Errorf("complete analysis QPE: %w", err)
	}
	return nil
}

func completionRunStatus(jobType string) workflow.RunStatus {
	switch jobType {
	case "preprocess":
		return workflow.RunBaselineRunning
	case "product.build":
		return workflow.RunPublished
	case "verification.run":
		return workflow.RunVerified
	default:
		return workflow.RunBaselineReady
	}
}

const runSelect = `
SELECT run_id, issue_time, grid_id, config_version, status,
       CASE WHEN status = 'DEGRADED' THEN reason ELSE NULL END,
       rerun_of, created_at, updated_at
FROM forecast_runs`

type rowScanner interface {
	Scan(...any) error
}

func scanRun(row rowScanner) (workflow.Run, error) {
	var run workflow.Run
	if err := row.Scan(
		&run.ID, &run.IssueTime, &run.GridID, &run.ConfigVersion, &run.Status,
		&run.DegradedReason, &run.RerunOf, &run.CreatedAt, &run.UpdatedAt,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.Run{}, workflow.ErrNotFound
		}
		return workflow.Run{}, fmt.Errorf("scan forecast run: %w", err)
	}
	return run, nil
}

const jobSelect = `
SELECT j.job_id, j.run_id, j.trace_id, j.job_type,
       COALESCE(j.model_id, ''), COALESCE(j.model_version, ''),
       j.config_version, j.status,
       COALESCE(attempt.attempt_no, 1),
       COALESCE(attempt.started_at, j.started_at),
       COALESCE(attempt.completed_at, j.completed_at),
       CASE WHEN attempt.metadata ? 'runtime_ms'
            THEN (attempt.metadata->>'runtime_ms')::bigint ELSE NULL END,
       attempt.error_code, attempt.error_message,
       j.request_payload, j.created_at
FROM jobs AS j
LEFT JOIN LATERAL (
    SELECT a.* FROM job_attempts AS a
    WHERE a.job_id = j.job_id
    ORDER BY a.attempt_no DESC LIMIT 1
) AS attempt ON TRUE`

func scanJob(row rowScanner) (workflow.Job, error) {
	var job workflow.Job
	if err := row.Scan(
		&job.ID, &job.RunID, &job.TraceID, &job.JobType,
		&job.ModelID, &job.ModelVersion, &job.ConfigVersion, &job.Status,
		&job.Attempt, &job.StartedAt, &job.FinishedAt, &job.RuntimeMS,
		&job.ErrorCode, &job.ErrorMessage, &job.RequestPayload, &job.CreatedAt,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.Job{}, workflow.ErrNotFound
		}
		return workflow.Job{}, fmt.Errorf("scan forecast job: %w", err)
	}
	return job, nil
}

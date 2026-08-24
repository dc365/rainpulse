package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
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
	err = tx.QueryRow(ctx, `
UPDATE jobs
SET status = CASE WHEN status = 'PENDING' THEN 'RUNNING' ELSE status END,
    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
    updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1
RETURNING run_id`, jobID).Scan(&runID)
	if err != nil {
		return fmt.Errorf("mark published job running: %w", err)
	}
	if _, err := tx.Exec(ctx, `UPDATE forecast_runs SET updated_at = CURRENT_TIMESTAMP WHERE run_id = $1`, runID); err != nil {
		return fmt.Errorf("touch published run: %w", err)
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
	err = tx.QueryRow(ctx, `
SELECT run_id, trace_id, job_type, status
FROM jobs WHERE job_id = $1 FOR UPDATE`, event.JobID).
		Scan(&runID, &traceID, &jobType, &jobStatus)
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
		"runtime_ms": event.Payload.RuntimeMS,
		"assets":     event.Payload.Assets,
		"metrics":    event.Payload.Metrics,
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
	err = tx.QueryRow(ctx, `
SELECT run_id, trace_id, status
FROM jobs WHERE job_id = $1 FOR UPDATE`, event.JobID).
		Scan(&runID, &traceID, &jobStatus)
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

	if err := tx.Commit(ctx); err != nil {
		return false, fmt.Errorf("commit failure transaction: %w", err)
	}
	return true, nil
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

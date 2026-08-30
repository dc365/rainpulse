package postgres

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operationalissues"
)

func (store *Store) OperationalIssues(
	ctx context.Context,
	limit int,
) (operationalissues.Snapshot, error) {
	if limit <= 0 || limit > 200 {
		return operationalissues.Snapshot{}, fmt.Errorf("operational issue limit must be between 1 and 200")
	}
	observedAt := time.Now().UTC()
	jobs, err := store.jobIssues(ctx, limit)
	if err != nil {
		return operationalissues.Snapshot{}, err
	}
	outbox, err := store.outboxIssues(ctx, limit)
	if err != nil {
		return operationalissues.Snapshot{}, err
	}
	items := append(jobs, outbox...)
	sort.Slice(items, func(left int, right int) bool {
		if !items[left].UpdatedAt.Equal(items[right].UpdatedAt) {
			return items[left].UpdatedAt.After(items[right].UpdatedAt)
		}
		return items[left].ID < items[right].ID
	})
	if len(items) > limit {
		items = items[:limit]
	}
	snapshot := operationalissues.Snapshot{Items: items, ObservedAt: observedAt}
	for _, item := range items {
		snapshot.Counts.Total++
		switch {
		case item.Kind == operationalissues.KindOutbox:
			snapshot.Counts.OutboxEvents++
		case item.Status == "FAILED":
			snapshot.Counts.FailedJobs++
		default:
			snapshot.Counts.StuckJobs++
		}
	}
	return snapshot, nil
}

func (store *Store) jobIssues(ctx context.Context, limit int) ([]operationalissues.Issue, error) {
	rows, err := store.pool.Query(ctx, `
SELECT j.job_id::text, j.run_id::text, j.job_type, j.status,
       COALESCE(attempt.attempt_no, 1), attempt.error_code, attempt.error_message,
       GREATEST(0, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(attempt.started_at, j.started_at, j.scheduled_at, j.created_at))))::double precision,
       j.created_at,
       COALESCE(attempt.completed_at, j.completed_at, j.updated_at)
FROM jobs AS j
LEFT JOIN LATERAL (
    SELECT attempt_no, error_code, error_message, started_at, completed_at
    FROM job_attempts
    WHERE job_id = j.job_id
    ORDER BY attempt_no DESC
    LIMIT 1
) AS attempt ON TRUE
WHERE (
       j.status = 'FAILED'
       AND COALESCE(attempt.completed_at, j.completed_at, j.updated_at)
           >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
   )
   OR (
       j.status IN ('PENDING', 'RUNNING')
       AND COALESCE(attempt.started_at, j.started_at, j.scheduled_at, j.created_at)
           <= CURRENT_TIMESTAMP - INTERVAL '10 minutes'
   )
ORDER BY COALESCE(attempt.completed_at, j.completed_at, j.updated_at) DESC
LIMIT $1`, limit)
	if err != nil {
		return nil, fmt.Errorf("list failed or stuck jobs: %w", err)
	}
	defer rows.Close()
	items := make([]operationalissues.Issue, 0)
	for rows.Next() {
		var issue operationalissues.Issue
		var jobID string
		var runID string
		var jobType string
		if err := rows.Scan(
			&jobID, &runID, &jobType, &issue.Status, &issue.AttemptCount,
			&issue.ErrorCode, &issue.ErrorMessage, &issue.AgeSeconds,
			&issue.CreatedAt, &issue.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan job issue: %w", err)
		}
		issue.ID = "job:" + jobID
		issue.Kind = operationalissues.KindJob
		issue.JobID = &jobID
		issue.RunID = &runID
		issue.JobType = &jobType
		issue.ErrorMessage = truncateText(issue.ErrorMessage, 500)
		if issue.Status == "FAILED" {
			issue.Summary = jobType + " 执行失败"
		} else if issue.Status == "PENDING" {
			issue.Summary = jobType + " 等待调度超过 10 分钟"
		} else {
			issue.Summary = jobType + " 持续运行超过 10 分钟"
		}
		items = append(items, issue)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate job issues: %w", err)
	}
	return items, nil
}

func (store *Store) outboxIssues(ctx context.Context, limit int) ([]operationalissues.Issue, error) {
	rows, err := store.pool.Query(ctx, `
SELECT event.event_id::text, event.aggregate_id, event.event_type, event.status,
       event.attempt_count, event.last_error,
       GREATEST(0, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - event.created_at)))::double precision,
       event.created_at, event.available_at,
       job.job_id::text, job.run_id::text, job.job_type
FROM outbox_events AS event
LEFT JOIN jobs AS job ON job.job_id::text = event.aggregate_id
WHERE event.status <> 'published'
  AND event.created_at <= CURRENT_TIMESTAMP - INTERVAL '1 minute'
ORDER BY event.created_at DESC
LIMIT $1`, limit)
	if err != nil {
		return nil, fmt.Errorf("list unpublished outbox issues: %w", err)
	}
	defer rows.Close()
	items := make([]operationalissues.Issue, 0)
	for rows.Next() {
		var issue operationalissues.Issue
		var eventID string
		var eventType string
		var aggregateID string
		if err := rows.Scan(
			&eventID, &aggregateID, &eventType, &issue.Status, &issue.AttemptCount,
			&issue.ErrorMessage, &issue.AgeSeconds, &issue.CreatedAt, &issue.UpdatedAt,
			&issue.JobID, &issue.RunID, &issue.JobType,
		); err != nil {
			return nil, fmt.Errorf("scan outbox issue: %w", err)
		}
		issue.ID = "outbox:" + eventID
		issue.Kind = operationalissues.KindOutbox
		issue.EventID = &eventID
		issue.AggregateID = &aggregateID
		issue.EventType = &eventType
		issue.ErrorMessage = truncateText(issue.ErrorMessage, 500)
		issue.Summary = eventType + " 尚未发布"
		items = append(items, issue)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate outbox issues: %w", err)
	}
	return items, nil
}

func truncateText(value *string, limit int) *string {
	if value == nil {
		return nil
	}
	trimmed := strings.TrimSpace(*value)
	runes := []rune(trimmed)
	if len(runes) > limit {
		trimmed = string(runes[:limit])
	}
	return &trimmed
}

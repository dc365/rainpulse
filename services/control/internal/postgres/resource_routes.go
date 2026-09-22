package postgres

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workloads"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// RouteRequestedEvent freezes the route in the EXISTING canonical outbox row,
// before publishing to NATS. Two additive outbox columns also freeze a
// realtime decision, so retries cannot change lane after a configuration change.
// The row lock serializes concurrent dispatch and replay. A published legacy
// event is never migrated implicitly: old replays retain their original lane.
func (store *Store) RouteRequestedEvent(ctx context.Context, event workflow.OutboxEvent, settings workloads.Settings) (workflow.OutboxEvent, error) {
	base, supported := workloads.BaseSubject(event.Subject)
	if !supported {
		return event, nil
	}
	var request struct {
		JobID     uuid.UUID `json:"job_id"`
		EventType string    `json:"event_type"`
	}
	if err := json.Unmarshal(event.Payload, &request); err != nil || request.JobID == uuid.Nil || request.EventType == "" {
		return event, fmt.Errorf("resource routing requires a valid job request identity")
	}
	bounded, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	tx, err := store.pool.BeginTx(bounded, pgx.TxOptions{})
	if err != nil {
		return event, fmt.Errorf("begin resource route: %w", err)
	}
	defer func() { _ = tx.Rollback(bounded) }()
	var canonicalID uuid.UUID
	var canonicalSubject, canonicalStatus string
	var frozen bool
	var source *time.Time
	var metadata workloads.Metadata
	err = tx.QueryRow(bounded, canonicalResourceRouteSQL, request.JobID, request.EventType).Scan(
		&canonicalID, &canonicalSubject, &canonicalStatus, &frozen, &metadata.CreatedAt, &source, &metadata.Regeneration,
	)
	if err != nil {
		return event, fmt.Errorf("load canonical job resource route: %w", err)
	}
	canonicalBase, supported := workloads.BaseSubject(canonicalSubject)
	if !supported || canonicalBase != base {
		return event, fmt.Errorf("requested resource route differs from canonical outbox")
	}
	if source != nil {
		metadata.SourceTime = *source
	}
	decision := workloads.Decision{Subject: canonicalSubject, Lane: "realtime", Reason: "persisted_route"}
	if !frozen && canonicalStatus != "published" && !strings.HasPrefix(canonicalSubject, workloads.BackgroundPrefix) {
		decision = workloads.Select(canonicalSubject, metadata, settings)
	}
	if !frozen {
		if _, err := tx.Exec(bounded, `UPDATE outbox_events SET subject=$2, resource_route_frozen=TRUE, resource_route_reason=$3 WHERE event_id=$1`, canonicalID, decision.Subject, decision.Reason); err != nil {
			return event, fmt.Errorf("freeze resource route: %w", err)
		}
	}
	if err := tx.Commit(bounded); err != nil {
		return event, fmt.Errorf("commit resource route: %w", err)
	}
	event.Subject = decision.Subject
	if decision.Subject != canonicalSubject {
		slog.Info("job resource route frozen", "job_id", request.JobID, "lane", decision.Lane, "reason", decision.Reason)
	}
	return event, nil
}

// RequestedRouteForReplay uses the original persisted subject even when lane
// creation has since been disabled. Do not republish an old job to two queues.
func (store *Store) RequestedRouteForReplay(ctx context.Context, jobID uuid.UUID, eventType string) (string, error) {
	var subject string
	err := store.pool.QueryRow(ctx, `SELECT subject FROM outbox_events
WHERE aggregate_type='job' AND aggregate_id=$1 AND event_type=$2
  AND subject LIKE 'rainpulse.jobs.requested.%'
ORDER BY created_at, event_id LIMIT 1`, jobID.String(), eventType).Scan(&subject)
	if err != nil {
		return "", fmt.Errorf("original replay route is unavailable; create an explicit regeneration instead: %w", err)
	}
	return subject, nil
}

const canonicalResourceRouteSQL = `SELECT o.event_id, o.subject, o.status, o.resource_route_frozen, j.created_at,
       COALESCE(s.volume_end_time, a.analysis_time, f.issue_time),
       (j.regeneration_request_id IS NOT NULL OR f.rerun_of IS NOT NULL)
FROM jobs j
JOIN outbox_events o ON o.aggregate_type='job' AND o.aggregate_id=j.job_id::text
LEFT JOIN radar_scan_runs r ON r.run_id=j.run_id
LEFT JOIN radar_scans s ON s.scan_id=r.scan_id
LEFT JOIN analysis_cycles a ON a.run_id=j.run_id
LEFT JOIN forecast_runs f ON f.run_id=j.run_id
WHERE j.job_id=$1 AND o.event_type=$2 AND o.subject LIKE 'rainpulse.jobs.requested.%'
ORDER BY o.created_at, o.event_id
LIMIT 1 FOR UPDATE OF o`

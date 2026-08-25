package postgres

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func applyNowcastInputCompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
) error {
	var inputAsset *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		if event.Payload.Assets[index].AssetType == "nowcast_input" {
			if inputAsset != nil {
				return fmt.Errorf("%w: multiple NowcastInput assets", orchestration.ErrInvalidEvent)
			}
			inputAsset = &event.Payload.Assets[index]
		}
	}
	if inputAsset == nil {
		return fmt.Errorf("%w: NowcastInput asset is required", orchestration.ErrInvalidEvent)
	}
	rawMetrics, ok := event.Payload.Diagnostics["nowcast_input"]
	if !ok {
		return fmt.Errorf("%w: NowcastInput diagnostics are required", orchestration.ErrInvalidEvent)
	}
	var metrics workflow.NowcastInputMetrics
	if err := json.Unmarshal(rawMetrics, &metrics); err != nil {
		return fmt.Errorf("%w: decode NowcastInput diagnostics: %v", orchestration.ErrInvalidEvent, err)
	}
	if metrics.SchemaVersion != "1.0" || metrics.IssueTimeUTC.IsZero() ||
		metrics.GridID == "" || metrics.ProfileVersion == "" ||
		metrics.PreprocessVersion == "" || metrics.FrameCount < 3 ||
		metrics.FrameCount > 6 || metrics.TimestepMinutes != 5 ||
		len(metrics.AnalysisIDs) != metrics.FrameCount ||
		len(metrics.InputAssetIDs) == 0 ||
		len(metrics.InputURIs) != metrics.FrameCount ||
		metrics.ValidCoverageRatio < 0 || metrics.ValidCoverageRatio > 1 ||
		metrics.MeanQualityIndex < 0 || metrics.MeanQualityIndex > 1 ||
		metrics.MaxDataAgeMinutes < 0 || metrics.ValidCellCount < 0 ||
		metrics.MissingCellCount < 0 || metrics.LowQualityCellCount < 0 ||
		metrics.LowQualityCellCount > metrics.ValidCellCount ||
		!metrics.OperationalEligible || len(metrics.OperationalReasons) != 0 {
		return fmt.Errorf("%w: invalid NowcastInput diagnostics", orchestration.ErrInvalidEvent)
	}
	var issueTime time.Time
	var gridID, preprocessVersion, gateConfigVersion string
	var rawRequest json.RawMessage
	var currentStatus workflow.RunStatus
	if err := tx.QueryRow(ctx, `
SELECT n.issue_time, n.grid_id, n.preprocess_version, n.gate_config_version,
       j.request_payload, f.status
FROM nowcast_input_runs AS n
JOIN jobs AS j ON j.job_id = n.job_id
JOIN forecast_runs AS f ON f.run_id = n.run_id
WHERE n.job_id = $1 AND n.run_id = $2
FOR UPDATE OF n, f`, event.JobID, event.RunID).Scan(
		&issueTime, &gridID, &preprocessVersion, &gateConfigVersion,
		&rawRequest, &currentStatus,
	); err != nil {
		return fmt.Errorf("lock NowcastInput completion: %w", err)
	}
	if currentStatus != workflow.RunPreprocessing {
		return fmt.Errorf("%w: forecast run is not preprocessing", orchestration.ErrInvalidEvent)
	}
	var requested orchestration.NowcastInputRequested
	if err := json.Unmarshal(rawRequest, &requested); err != nil {
		return fmt.Errorf("decode stored NowcastInput request: %w", err)
	}
	if !metrics.IssueTimeUTC.Equal(issueTime) || !requested.Payload.IssueTime.Equal(issueTime) ||
		metrics.GridID != gridID || requested.Payload.GridID != gridID ||
		metrics.PreprocessVersion != preprocessVersion ||
		requested.Payload.PreprocessVersion != preprocessVersion ||
		metrics.ProfileVersion != gateConfigVersion ||
		requested.Payload.GateConfigVersion != gateConfigVersion ||
		len(requested.Payload.AnalysisIDs) != metrics.FrameCount ||
		len(requested.Payload.InputURIs) != metrics.FrameCount {
		return fmt.Errorf("%w: NowcastInput identity differs from request", orchestration.ErrInvalidEvent)
	}
	rows, err := tx.Query(ctx, `
SELECT analysis_id, analysis_time, input_uri
FROM nowcast_input_frames WHERE job_id = $1 ORDER BY frame_index`, event.JobID)
	if err != nil {
		return fmt.Errorf("load persisted NowcastInput frames: %w", err)
	}
	defer rows.Close()
	index := 0
	for rows.Next() {
		var analysisID uuid.UUID
		var analysisTime time.Time
		var inputURI string
		if err := rows.Scan(&analysisID, &analysisTime, &inputURI); err != nil {
			return fmt.Errorf("scan persisted NowcastInput frame: %w", err)
		}
		if index >= metrics.FrameCount || analysisID != metrics.AnalysisIDs[index] ||
			analysisID != requested.Payload.AnalysisIDs[index] ||
			inputURI != metrics.InputURIs[index] || inputURI != requested.Payload.InputURIs[index] ||
			!analysisTime.Equal(issueTime.Add(time.Duration(index-metrics.FrameCount+1)*5*time.Minute)) {
			return fmt.Errorf("%w: NowcastInput frame provenance differs", orchestration.ErrInvalidEvent)
		}
		index++
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate persisted NowcastInput frames: %w", err)
	}
	if index != metrics.FrameCount {
		return fmt.Errorf("%w: NowcastInput frame count differs", orchestration.ErrInvalidEvent)
	}
	metrics.MeasuredAt = event.Payload.FinishedAt
	diagnostics, err := json.Marshal(metrics)
	if err != nil {
		return fmt.Errorf("encode NowcastInput diagnostics: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE nowcast_input_runs
SET status = 'SUCCEEDED', input_uri = $2, diagnostics = $3,
    measured_at = $4, updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1`, event.JobID, inputAsset.URI, diagnostics,
		metrics.MeasuredAt); err != nil {
		return fmt.Errorf("persist NowcastInput completion: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE forecast_runs
SET status = 'INPUT_READY', updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, event.RunID); err != nil {
		return fmt.Errorf("mark forecast input ready: %w", err)
	}
	readyEvent := orchestration.NowcastInputReady{
		SchemaVersion: orchestration.SchemaVersion,
		EventID: uuid.NewSHA1(
			uuid.NameSpaceURL,
			[]byte("rainpulse:nowcast-input-ready:"+event.JobID.String()),
		),
		EventType:  orchestration.NowcastInputReadyEventType,
		OccurredAt: event.Payload.FinishedAt,
		RunID:      event.RunID,
		JobID:      event.JobID,
		TraceID:    event.TraceID,
		Payload: orchestration.NowcastInputReadyPayload{
			InputURI: inputAsset.URI, IssueTime: issueTime, GridID: gridID,
			AnalysisIDs: metrics.AnalysisIDs, FrameCount: metrics.FrameCount,
			TimestepMinutes:    metrics.TimestepMinutes,
			ValidCoverageRatio: metrics.ValidCoverageRatio,
			MeanQualityIndex:   metrics.MeanQualityIndex,
			MaxDataAgeMinutes:  metrics.MaxDataAgeMinutes,
			PreprocessVersion:  preprocessVersion,
		},
	}
	readyPayload, err := json.Marshal(readyEvent)
	if err != nil {
		return fmt.Errorf("encode NowcastInput ready event: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)
ON CONFLICT (event_id) DO NOTHING`, readyEvent.EventID, event.JobID.String(),
		readyEvent.EventType, orchestration.NowcastInputReadySubject,
		readyPayload, readyEvent.OccurredAt); err != nil {
		return fmt.Errorf("insert NowcastInput ready outbox event: %w", err)
	}
	return nil
}

func (store *Store) ListNowcastInputCandidates(
	ctx context.Context,
	issueTime time.Time,
	gridID string,
	maximumFrames int,
) ([]orchestration.NowcastInputCandidate, error) {
	if issueTime.IsZero() || gridID == "" || maximumFrames < 3 || maximumFrames > 6 {
		return nil, fmt.Errorf("invalid NowcastInput candidate query")
	}
	rows, err := store.pool.Query(ctx, `
SELECT analysis_id, analysis_time, grid_id, analysis_uri, status,
       degraded_reason IS NULL,
       COALESCE(valid_coverage_ratio, 0), COALESCE(mean_quality_index, 0)
FROM analysis_cycles
WHERE grid_id = $1
  AND analysis_time <= $2
  AND analysis_time >= $2 - ($3::int - 1) * INTERVAL '5 minutes'
  AND status = 'ANALYSIS_READY'
  AND analysis_uri IS NOT NULL
ORDER BY analysis_time DESC`, gridID, issueTime.UTC(), maximumFrames)
	if err != nil {
		return nil, fmt.Errorf("list NowcastInput RadarAnalysis candidates: %w", err)
	}
	defer rows.Close()
	candidates := make([]orchestration.NowcastInputCandidate, 0, maximumFrames)
	for rows.Next() {
		var candidate orchestration.NowcastInputCandidate
		if err := rows.Scan(
			&candidate.AnalysisID, &candidate.AnalysisTime, &candidate.GridID,
			&candidate.AnalysisURI, &candidate.CurrentStatus,
			&candidate.OperationalEligible, &candidate.ValidCoverageRatio,
			&candidate.MeanQualityIndex,
		); err != nil {
			return nil, fmt.Errorf("scan NowcastInput candidate: %w", err)
		}
		candidates = append(candidates, candidate)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate NowcastInput candidates: %w", err)
	}
	return candidates, nil
}

func (store *Store) CreateNowcastInputBundle(
	ctx context.Context,
	bundle workflow.NowcastInputBundle,
) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin NowcastInput transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err = tx.Exec(ctx, `
INSERT INTO config_versions (config_version, sha256, config, description, created_at)
VALUES ($1, $2, $3, 'RP-013 NowcastInput gate configuration', $4)
ON CONFLICT (config_version) DO NOTHING`, bundle.GateConfigVersion,
		bundle.ConfigSHA256, bundle.Config, bundle.Run.CreatedAt); err != nil {
		return fmt.Errorf("insert NowcastInput configuration: %w", err)
	}
	var storedHash string
	if err = tx.QueryRow(ctx, `SELECT sha256 FROM config_versions WHERE config_version = $1`,
		bundle.GateConfigVersion).Scan(&storedHash); err != nil {
		return fmt.Errorf("verify NowcastInput configuration: %w", err)
	}
	if storedHash != bundle.ConfigSHA256 {
		return fmt.Errorf("NowcastInput config version already has a different SHA-256")
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO workflow_runs (run_id, run_type, created_at)
VALUES ($1, 'forecast_run', $2)
ON CONFLICT (run_id) DO NOTHING`, bundle.Run.ID, bundle.Run.CreatedAt); err != nil {
		return fmt.Errorf("insert NowcastInput workflow identity: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO forecast_runs (
    run_id, issue_time, grid_id, config_version, status, reason, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, 'RP-013 fixed-step NowcastInput', $6, $6)
ON CONFLICT (run_id) DO NOTHING`, bundle.Run.ID, bundle.Run.IssueTime,
		bundle.Run.GridID, bundle.Run.ConfigVersion, bundle.Run.Status,
		bundle.Run.CreatedAt); err != nil {
		return fmt.Errorf("insert RP-013 forecast run: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO jobs (
    job_id, run_id, trace_id, job_type, model_id, model_version,
    config_version, status, max_attempts, scheduled_at, created_at, updated_at,
    request_payload
) VALUES ($1, $2, $3, $4, NULL, NULL, $5, $6, 3, $7, $7, $7, $8)
ON CONFLICT (job_id) DO NOTHING`, bundle.Job.ID, bundle.Job.RunID,
		bundle.Job.TraceID, bundle.Job.JobType, bundle.Job.ConfigVersion,
		bundle.Job.Status, bundle.Job.CreatedAt, bundle.Job.RequestPayload); err != nil {
		return fmt.Errorf("insert NowcastInput job: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO nowcast_input_runs (
    job_id, run_id, issue_time, grid_id, preprocess_version,
    gate_config_version, status, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, 'RUNNING', $7, $7)
ON CONFLICT (job_id) DO NOTHING`, bundle.Job.ID, bundle.Run.ID,
		bundle.Run.IssueTime, bundle.Run.GridID, bundle.PreprocessVersion,
		bundle.GateConfigVersion, bundle.Run.CreatedAt); err != nil {
		return fmt.Errorf("insert NowcastInput run: %w", err)
	}
	for index, frame := range bundle.Frames {
		var status workflow.AnalysisStatus
		var analysisTime time.Time
		var gridID, analysisURI string
		var degradedReason *string
		if err = tx.QueryRow(ctx, `
SELECT status, analysis_time, grid_id, COALESCE(analysis_uri, ''), degraded_reason
FROM analysis_cycles WHERE analysis_id = $1 FOR SHARE`, frame.AnalysisID).Scan(
			&status, &analysisTime, &gridID, &analysisURI, &degradedReason,
		); err != nil {
			return fmt.Errorf("verify NowcastInput frame %s: %w", frame.AnalysisID, err)
		}
		if status != workflow.AnalysisReady || !analysisTime.Equal(frame.AnalysisTime) ||
			gridID != bundle.Run.GridID || analysisURI != frame.InputURI || degradedReason != nil {
			return fmt.Errorf("NowcastInput frame %s is not a committed operational RadarAnalysis", frame.AnalysisID)
		}
		if _, err = tx.Exec(ctx, `
INSERT INTO nowcast_input_frames (
    job_id, frame_index, analysis_id, analysis_time, input_uri
) VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (job_id, frame_index) DO NOTHING`, bundle.Job.ID, index,
			frame.AnalysisID, frame.AnalysisTime, frame.InputURI); err != nil {
			return fmt.Errorf("insert NowcastInput frame %d: %w", index, err)
		}
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)
ON CONFLICT (event_id) DO NOTHING`, bundle.Outbox.ID, bundle.Outbox.AggregateID,
		bundle.Outbox.EventType, bundle.Outbox.Subject, bundle.Outbox.Payload,
		bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert NowcastInput outbox event: %w", err)
	}
	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit NowcastInput transaction: %w", err)
	}
	return nil
}

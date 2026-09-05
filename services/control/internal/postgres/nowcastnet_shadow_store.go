package postgres

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func (store *Store) GetNowcastNetShadowInput(
	ctx context.Context,
	runID uuid.UUID,
) (orchestration.NowcastNetShadowInput, error) {
	var input orchestration.NowcastNetShadowInput
	input.RunID = runID
	if err := store.pool.QueryRow(ctx, `
SELECT issue_time, grid_id, status, rerun_of IS NOT NULL
FROM forecast_runs
WHERE run_id = $1`, runID).Scan(&input.IssueTime, &input.GridID, &input.CurrentStatus, &input.HistoricalRegeneration); err != nil {
		if err == pgx.ErrNoRows {
			return orchestration.NowcastNetShadowInput{}, workflow.ErrNotFound
		}
		return orchestration.NowcastNetShadowInput{}, fmt.Errorf("load NowcastNet shadow run: %w", err)
	}

	expectedTimes := nowcastNetShadowInputTimes(input.IssueTime)
	rows, err := store.pool.Query(ctx, nowcastNetShadowAnalysisFramesQuery, input.GridID, expectedTimes)
	if err != nil {
		return orchestration.NowcastNetShadowInput{}, fmt.Errorf("load NowcastNet shadow analysis frames: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var frame workflow.NowcastNetShadowInputFrame
		if err := rows.Scan(&frame.AnalysisID, &frame.AnalysisTime, &frame.AnalysisURI); err != nil {
			return orchestration.NowcastNetShadowInput{}, fmt.Errorf("scan NowcastNet shadow analysis frame: %w", err)
		}
		input.InputFrames = append(input.InputFrames, frame)
	}
	if err := rows.Err(); err != nil {
		return orchestration.NowcastNetShadowInput{}, fmt.Errorf("iterate NowcastNet shadow analysis frames: %w", err)
	}
	return input, nil
}

// A full historical rerun creates a newer analysis lineage at the same valid
// times as the retained source lineage.  NowcastNet needs one frame per time,
// so prefer the newest completed analysis rather than returning both versions.
const nowcastNetShadowAnalysisFramesQuery = `
SELECT analysis_id, analysis_time, analysis_uri
FROM (
    SELECT DISTINCT ON (analysis_time)
           analysis_id, analysis_time, analysis_uri, created_at
    FROM analysis_cycles
    WHERE grid_id = $1
      AND status = 'ANALYSIS_READY'
      AND analysis_time = ANY($2::timestamptz[])
      AND analysis_uri IS NOT NULL
    ORDER BY analysis_time, created_at DESC
) AS preferred
ORDER BY analysis_time`

func nowcastNetShadowInputTimes(issueTime time.Time) []time.Time {
	issueTime = issueTime.UTC()
	values := make([]time.Time, 9)
	for index := range values {
		values[index] = issueTime.Add(time.Duration(-80+10*index) * time.Minute)
	}
	return values
}

func (store *Store) CreateNowcastNetShadowBundle(
	ctx context.Context,
	bundle workflow.NowcastNetShadowBundle,
) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin NowcastNet shadow transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err = tx.Exec(ctx, `
INSERT INTO config_versions (config_version, sha256, config, description, created_at)
VALUES ($1, $2, $3, 'NowcastNet public-weight shadow configuration registered by forecast workflow', $4)
ON CONFLICT (config_version) DO NOTHING`, bundle.Job.ConfigVersion, bundle.ConfigSHA256, bundle.Config, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert NowcastNet shadow configuration: %w", err)
	}
	var storedHash string
	if err = tx.QueryRow(ctx, `SELECT sha256 FROM config_versions WHERE config_version = $1`, bundle.Job.ConfigVersion).Scan(&storedHash); err != nil {
		return fmt.Errorf("verify NowcastNet shadow configuration: %w", err)
	}
	if storedHash != bundle.ConfigSHA256 {
		return fmt.Errorf("NowcastNet shadow config version already has a different SHA-256")
	}
	modelMetadata, err := json.Marshal(map[string]any{
		"lifecycle": "shadow", "native_lead_step_minutes": 10,
		"display_lead_step_minutes": 5, "tile_atlas": "fujian-nowcastnet-tile-atlas-v1",
		"source_model_config_version": "rp026-nowcastnet-offline-v1",
	})
	if err != nil {
		return fmt.Errorf("encode NowcastNet shadow model metadata: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO model_versions (
    model_id, model_version, model_type, config_version, enabled, metadata, created_at
) VALUES ($1, $2, 'public_weight_shadow', $3, FALSE, $4, $5)
ON CONFLICT (model_id, model_version) DO NOTHING`, bundle.Job.ModelID, bundle.Job.ModelVersion,
		bundle.Job.ConfigVersion, modelMetadata, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert NowcastNet shadow model version: %w", err)
	}
	var modelConfig string
	if err = tx.QueryRow(ctx, `
SELECT config_version FROM model_versions WHERE model_id = $1 AND model_version = $2`,
		bundle.Job.ModelID, bundle.Job.ModelVersion).Scan(&modelConfig); err != nil {
		return fmt.Errorf("verify NowcastNet shadow model version: %w", err)
	}
	if modelConfig != bundle.Job.ConfigVersion {
		return fmt.Errorf("NowcastNet shadow model version already uses a different configuration")
	}

	var currentStatus workflow.RunStatus
	var issueTime time.Time
	var gridID string
	var historicalRegeneration bool
	if err = tx.QueryRow(ctx, `
SELECT status, issue_time, grid_id, rerun_of IS NOT NULL
FROM forecast_runs WHERE run_id = $1 FOR UPDATE`, bundle.Run.ID).Scan(&currentStatus, &issueTime, &gridID, &historicalRegeneration); err != nil {
		return fmt.Errorf("lock NowcastNet shadow forecast run: %w", err)
	}
	if (currentStatus != workflow.RunInputReady &&
		!(historicalRegeneration && currentStatus == workflow.RunPublished)) ||
		!issueTime.Equal(bundle.Run.IssueTime) || gridID != bundle.Run.GridID {
		return fmt.Errorf("NowcastNet shadow requires the committed INPUT_READY forecast run")
	}
	if len(bundle.InputFrames) != 9 {
		return fmt.Errorf("NowcastNet shadow requires nine direct analysis frames")
	}
	analysisIDs := make([]uuid.UUID, len(bundle.InputFrames))
	analysisURIs := make([]string, len(bundle.InputFrames))
	for index, frame := range bundle.InputFrames {
		var storedTime time.Time
		var storedURI string
		var status string
		if err = tx.QueryRow(ctx, `
SELECT analysis_time, analysis_uri, status
FROM analysis_cycles WHERE analysis_id = $1 FOR UPDATE`, frame.AnalysisID).Scan(&storedTime, &storedURI, &status); err != nil {
			return fmt.Errorf("lock NowcastNet shadow analysis frame %d: %w", index, err)
		}
		if status != "ANALYSIS_READY" || !storedTime.Equal(frame.AnalysisTime) || storedURI != frame.AnalysisURI ||
			!storedTime.Equal(bundle.Run.IssueTime.Add(time.Duration(-80+10*index)*time.Minute)) {
			return fmt.Errorf("NowcastNet shadow analysis frame %d changed before scheduling", index)
		}
		analysisIDs[index] = frame.AnalysisID
		analysisURIs[index] = frame.AnalysisURI
	}

	jobResult, err := tx.Exec(ctx, `
INSERT INTO jobs (
    job_id, run_id, trace_id, job_type, model_id, model_version,
    config_version, status, max_attempts, scheduled_at, created_at, updated_at, request_payload
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 3, $9, $9, $9, $10)
ON CONFLICT (job_id) DO NOTHING`,
		bundle.Job.ID, bundle.Job.RunID, bundle.Job.TraceID, bundle.Job.JobType, bundle.Job.ModelID,
		bundle.Job.ModelVersion, bundle.Job.ConfigVersion, bundle.Job.Status, bundle.Job.CreatedAt,
		bundle.Job.RequestPayload)
	if err != nil {
		return fmt.Errorf("insert NowcastNet shadow job: %w", err)
	}
	if jobResult.RowsAffected() == 0 {
		var storedRunID uuid.UUID
		var storedJobType, storedModelID, storedModelVersion, storedConfigVersion string
		if err = tx.QueryRow(ctx, `
SELECT run_id, job_type, COALESCE(model_id, ''), COALESCE(model_version, ''), config_version
FROM jobs
WHERE job_id = $1
FOR UPDATE`, bundle.Job.ID).Scan(
			&storedRunID, &storedJobType, &storedModelID, &storedModelVersion, &storedConfigVersion,
		); err != nil {
			return fmt.Errorf("lock existing NowcastNet shadow job: %w", err)
		}
		if storedRunID != bundle.Run.ID || storedJobType != bundle.Job.JobType ||
			storedModelID != bundle.Job.ModelID || storedModelVersion != bundle.Job.ModelVersion ||
			storedConfigVersion != bundle.Job.ConfigVersion {
			return fmt.Errorf("existing NowcastNet shadow job identity differs")
		}
		var storedAlgorithmRunID uuid.UUID
		if err = tx.QueryRow(ctx, `
SELECT algorithm_run_id
FROM algorithm_runs
WHERE job_id = $1
FOR UPDATE`, bundle.Job.ID).Scan(&storedAlgorithmRunID); err != nil {
			return fmt.Errorf("lock existing NowcastNet shadow algorithm run: %w", err)
		}
		if storedAlgorithmRunID != bundle.AlgorithmRunID {
			return fmt.Errorf("existing NowcastNet shadow algorithm run identity differs")
		}
		if err = tx.Commit(ctx); err != nil {
			return fmt.Errorf("commit existing NowcastNet shadow transaction: %w", err)
		}
		return nil
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO algorithm_runs (
    algorithm_run_id, run_id, job_id, algorithm_id, model_version, config_version,
    source_model_config_version, tile_atlas_version, input_analysis_ids, input_analysis_uris,
    status, started_at
) VALUES ($1, $2, $3, 'nowcastnet', $4, $5, $6, $7, $8, $9, 'running', $10)`,
		bundle.AlgorithmRunID, bundle.Run.ID, bundle.Job.ID, bundle.Job.ModelVersion,
		bundle.Job.ConfigVersion, "rp026-nowcastnet-offline-v1", "fujian-nowcastnet-tile-atlas-v1",
		analysisIDs, analysisURIs, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert NowcastNet algorithm run: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)`,
		bundle.Outbox.ID, bundle.Outbox.AggregateID, bundle.Outbox.EventType, bundle.Outbox.Subject,
		bundle.Outbox.Payload, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert NowcastNet shadow outbox event: %w", err)
	}
	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit NowcastNet shadow transaction: %w", err)
	}
	return nil
}

func applyNowcastNetShadowCompletion(ctx context.Context, tx pgx.Tx, event orchestration.JobCompleted) error {
	var bundle *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		if event.Payload.Assets[index].AssetType != "nowcastnet_shadow_product_bundle" {
			continue
		}
		if bundle != nil {
			return fmt.Errorf("%w: multiple NowcastNet shadow product bundles", orchestration.ErrInvalidEvent)
		}
		bundle = &event.Payload.Assets[index]
	}
	if bundle == nil || bundle.URI == "" || len(bundle.SHA256) != 64 {
		return fmt.Errorf("%w: NowcastNet shadow product bundle is required", orchestration.ErrInvalidEvent)
	}
	var algorithmRunID uuid.UUID
	var issueTime time.Time
	var gridID, modelVersion, configVersion, status string
	var inputIDs []uuid.UUID
	var inputURIs []string
	var rawRequest json.RawMessage
	if err := tx.QueryRow(ctx, `
SELECT ar.algorithm_run_id, f.issue_time, f.grid_id, ar.model_version, ar.config_version,
       ar.input_analysis_ids, ar.input_analysis_uris, ar.status, j.request_payload
FROM algorithm_runs AS ar
JOIN forecast_runs AS f ON f.run_id = ar.run_id
JOIN jobs AS j ON j.job_id = ar.job_id
WHERE ar.job_id = $1 AND ar.run_id = $2
FOR UPDATE OF ar`, event.JobID, event.RunID).Scan(
		&algorithmRunID, &issueTime, &gridID, &modelVersion, &configVersion,
		&inputIDs, &inputURIs, &status, &rawRequest,
	); err != nil {
		return fmt.Errorf("lock NowcastNet shadow completion: %w", err)
	}
	if status != "running" {
		return fmt.Errorf("%w: NowcastNet shadow algorithm run is not running", orchestration.ErrInvalidEvent)
	}
	var requested orchestration.NowcastNetShadowRequested
	if err := json.Unmarshal(rawRequest, &requested); err != nil {
		return fmt.Errorf("decode stored NowcastNet shadow request: %w", err)
	}
	if requested.EventType != orchestration.NowcastNetShadowRequestedEventType ||
		requested.Payload.AlgorithmRunID != algorithmRunID ||
		!requested.Payload.IssueTime.Equal(issueTime) || requested.Payload.GridID != gridID ||
		requested.Payload.ModelVersion != modelVersion || requested.Payload.ConfigVersion != configVersion ||
		len(requested.Payload.InputFrames) != len(inputIDs) ||
		bundle.URI != strings.TrimRight(requested.Payload.OutputPrefix, "/")+"/nowcastnet-shadow-products" {
		return fmt.Errorf("%w: NowcastNet shadow completion identity differs from request", orchestration.ErrInvalidEvent)
	}
	for index, frame := range requested.Payload.InputFrames {
		if frame.AnalysisID != inputIDs[index] || frame.AnalysisURI != inputURIs[index] {
			return fmt.Errorf("%w: NowcastNet shadow input provenance differs from request", orchestration.ErrInvalidEvent)
		}
	}
	diagnostics, ok := event.Payload.Diagnostics["nowcastnet_shadow"]
	if !ok || !json.Valid(diagnostics) {
		return fmt.Errorf("%w: NowcastNet shadow diagnostics are required", orchestration.ErrInvalidEvent)
	}
	var reported struct {
		AlgorithmRunID uuid.UUID `json:"algorithm_run_id"`
		JobID          uuid.UUID `json:"job_id"`
		RunID          uuid.UUID `json:"run_id"`
		NativeCadence  int       `json:"native_output_timestep_minutes"`
		DisplayCadence int       `json:"product_timestep_minutes"`
		LeadCount      int       `json:"product_lead_count"`
	}
	if err := json.Unmarshal(diagnostics, &reported); err != nil || reported.AlgorithmRunID != algorithmRunID ||
		reported.JobID != event.JobID || reported.RunID != event.RunID || reported.NativeCadence != 10 ||
		reported.DisplayCadence != 5 || reported.LeadCount != 24 {
		return fmt.Errorf("%w: invalid NowcastNet shadow diagnostics", orchestration.ErrInvalidEvent)
	}
	if _, err := tx.Exec(ctx, `
UPDATE algorithm_runs
SET status = 'completed', output_uri = $2, output_sha256 = $3, runtime_ms = $4,
    diagnostics = $5, completed_at = $6
WHERE algorithm_run_id = $1`, algorithmRunID, bundle.URI, bundle.SHA256, event.Payload.RuntimeMS,
		diagnostics, event.Payload.FinishedAt); err != nil {
		return fmt.Errorf("persist NowcastNet shadow completion: %w", err)
	}
	return nil
}

func applyNowcastNetShadowFailure(ctx context.Context, tx pgx.Tx, event orchestration.JobFailed) error {
	diagnostics, marshalErr := json.Marshal(map[string]any{
		"error_code":    event.Payload.ErrorCode,
		"error_message": event.Payload.ErrorMessage,
		"details":       event.Payload.Details,
	})
	if marshalErr != nil {
		return fmt.Errorf("encode NowcastNet shadow failure diagnostics: %w", marshalErr)
	}
	result, err := tx.Exec(ctx, `
UPDATE algorithm_runs
SET status = 'failed', runtime_ms = $2, diagnostics = $3, completed_at = $4
WHERE job_id = $1 AND status = 'running'`, event.JobID, event.Payload.RuntimeMS,
		diagnostics, event.Payload.FinishedAt)
	if err != nil {
		return fmt.Errorf("fail NowcastNet shadow algorithm run: %w", err)
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("%w: NowcastNet shadow algorithm run is not running", orchestration.ErrInvalidEvent)
	}
	return nil
}

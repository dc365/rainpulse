package postgres

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"slices"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func (store *Store) GetPystepsLKInput(
	ctx context.Context,
	runID uuid.UUID,
) (orchestration.PystepsLKInput, error) {
	var input orchestration.PystepsLKInput
	var diagnostics json.RawMessage
	input.RunID = runID
	err := store.pool.QueryRow(ctx, `
SELECT n.job_id, f.issue_time, f.grid_id, f.status, n.input_uri, n.diagnostics
FROM forecast_runs AS f
JOIN nowcast_input_runs AS n ON n.run_id = f.run_id
WHERE f.run_id = $1 AND n.status = 'SUCCEEDED' AND n.input_uri IS NOT NULL`, runID).Scan(
		&input.NowcastInputJobID, &input.IssueTime, &input.GridID,
		&input.CurrentStatus, &input.InputURI, &diagnostics,
	)
	if err == pgx.ErrNoRows {
		return orchestration.PystepsLKInput{}, workflow.ErrNotFound
	}
	if err != nil {
		return orchestration.PystepsLKInput{}, fmt.Errorf("load pySTEPS-LK input: %w", err)
	}
	input.InputAssetIDs, err = rawAssetIDsFromDiagnostics(diagnostics)
	if err != nil {
		return orchestration.PystepsLKInput{}, err
	}
	if len(input.InputAssetIDs) == 0 {
		input.InputAssetIDs, err = loadNowcastRawAssetIDs(ctx, store.pool, input.NowcastInputJobID)
		if err != nil {
			return orchestration.PystepsLKInput{}, err
		}
	}
	return input, nil
}

func rawAssetIDsFromDiagnostics(diagnostics json.RawMessage) ([]uuid.UUID, error) {
	var provenance struct {
		InputAssetIDs []uuid.UUID `json:"input_asset_ids"`
	}
	if len(diagnostics) == 0 {
		return nil, nil
	}
	if err := json.Unmarshal(diagnostics, &provenance); err != nil {
		return nil, fmt.Errorf("decode NowcastInput raw asset provenance: %w", err)
	}
	return provenance.InputAssetIDs, nil
}

type queryer interface {
	Query(context.Context, string, ...any) (pgx.Rows, error)
}

func loadNowcastRawAssetIDs(
	ctx context.Context,
	query queryer,
	nowcastJobID uuid.UUID,
) ([]uuid.UUID, error) {
	rows, err := query.Query(ctx, `
SELECT radar.raw_asset_id
FROM nowcast_input_frames AS frame
JOIN analysis_cycle_radars AS contributor
  ON contributor.analysis_id = frame.analysis_id
 AND contributor.state = 'PARTICIPATING'
JOIN radar_scans AS radar ON radar.scan_id = contributor.scan_id
WHERE frame.job_id = $1 AND radar.raw_asset_id IS NOT NULL
ORDER BY frame.frame_index, contributor.radar_id`, nowcastJobID)
	if err != nil {
		return nil, fmt.Errorf("load NowcastInput raw asset IDs: %w", err)
	}
	defer rows.Close()
	assets := make([]uuid.UUID, 0)
	seen := make(map[uuid.UUID]struct{})
	for rows.Next() {
		var assetID uuid.UUID
		if err := rows.Scan(&assetID); err != nil {
			return nil, fmt.Errorf("scan NowcastInput raw asset ID: %w", err)
		}
		if _, exists := seen[assetID]; !exists {
			seen[assetID] = struct{}{}
			assets = append(assets, assetID)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate NowcastInput raw asset IDs: %w", err)
	}
	if len(assets) == 0 {
		return nil, fmt.Errorf("NowcastInput has no committed raw asset provenance")
	}
	return assets, nil
}

func (store *Store) CreatePystepsLKBundle(
	ctx context.Context,
	bundle workflow.PystepsLKBundle,
) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin pySTEPS-LK transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err = tx.Exec(ctx, `
INSERT INTO config_versions (config_version, sha256, config, description, created_at)
VALUES ($1, $2, $3, 'pySTEPS-LK configuration registered by forecast workflow', $4)
ON CONFLICT (config_version) DO NOTHING`, bundle.Job.ConfigVersion,
		bundle.ConfigSHA256, bundle.Config, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert pySTEPS-LK configuration: %w", err)
	}
	var storedHash string
	if err = tx.QueryRow(ctx, `SELECT sha256 FROM config_versions WHERE config_version = $1`,
		bundle.Job.ConfigVersion).Scan(&storedHash); err != nil {
		return fmt.Errorf("verify pySTEPS-LK configuration: %w", err)
	}
	if storedHash != bundle.ConfigSHA256 {
		return fmt.Errorf("pySTEPS-LK config version already has a different SHA-256")
	}
	modelMetadata, err := json.Marshal(map[string]any{
		"forecast_contract_version": "1.1",
		"baselines":                 []string{"persistence", "translation"},
	})
	if err != nil {
		return fmt.Errorf("encode pySTEPS-LK model metadata: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO model_versions (
    model_id, model_version, model_type, config_version, enabled, metadata, created_at
) VALUES ($1, $2, 'deterministic_baseline', $3, TRUE, $4, $5)
ON CONFLICT (model_id, model_version) DO NOTHING`, bundle.Job.ModelID,
		bundle.Job.ModelVersion, bundle.Job.ConfigVersion, modelMetadata,
		bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert pySTEPS-LK model version: %w", err)
	}
	var modelConfig string
	if err = tx.QueryRow(ctx, `
SELECT config_version FROM model_versions WHERE model_id = $1 AND model_version = $2`,
		bundle.Job.ModelID, bundle.Job.ModelVersion).Scan(&modelConfig); err != nil {
		return fmt.Errorf("verify pySTEPS-LK model version: %w", err)
	}
	if modelConfig != bundle.Job.ConfigVersion {
		return fmt.Errorf("pySTEPS-LK model version already uses a different configuration")
	}

	var currentStatus workflow.RunStatus
	var issueTime time.Time
	var gridID, inputStatus, inputURI string
	var inputDiagnostics json.RawMessage
	if err = tx.QueryRow(ctx, `
SELECT f.status, f.issue_time, f.grid_id, n.status, COALESCE(n.input_uri, ''), n.diagnostics
FROM forecast_runs AS f
JOIN nowcast_input_runs AS n ON n.run_id = f.run_id
WHERE f.run_id = $1 AND n.job_id = $2
FOR UPDATE OF f, n`, bundle.Run.ID, bundle.NowcastInputJob).Scan(
		&currentStatus, &issueTime, &gridID, &inputStatus, &inputURI, &inputDiagnostics,
	); err != nil {
		return fmt.Errorf("lock pySTEPS-LK input: %w", err)
	}
	if currentStatus != workflow.RunInputReady || inputStatus != "SUCCEEDED" ||
		!issueTime.Equal(bundle.Run.IssueTime) || gridID != bundle.Run.GridID ||
		inputURI != bundle.InputURI {
		return fmt.Errorf("pySTEPS-LK input is not the committed INPUT_READY artifact")
	}
	storedAssets, err := rawAssetIDsFromDiagnostics(inputDiagnostics)
	if err != nil {
		return err
	}
	if len(storedAssets) == 0 {
		storedAssets, err = loadNowcastRawAssetIDs(ctx, tx, bundle.NowcastInputJob)
		if err != nil {
			return err
		}
	}
	if !slices.Equal(storedAssets, bundle.InputAssetIDs) {
		return fmt.Errorf("pySTEPS-LK raw asset provenance changed before scheduling")
	}

	if _, err = tx.Exec(ctx, `
INSERT INTO jobs (
    job_id, run_id, trace_id, job_type, model_id, model_version,
    config_version, status, max_attempts, scheduled_at, created_at, updated_at,
    request_payload
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 3, $9, $9, $9, $10)`,
		bundle.Job.ID, bundle.Job.RunID, bundle.Job.TraceID, bundle.Job.JobType,
		bundle.Job.ModelID, bundle.Job.ModelVersion, bundle.Job.ConfigVersion,
		bundle.Job.Status, bundle.Job.CreatedAt, bundle.Job.RequestPayload); err != nil {
		return fmt.Errorf("insert pySTEPS-LK job: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO model_runs (
    model_run_id, run_id, job_id, model_id, model_version, config_version,
    input_asset_ids, status, input_uri, metadata, started_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, 'running', $8, $9, $10)`,
		bundle.ModelRunID, bundle.Run.ID, bundle.Job.ID, bundle.Job.ModelID,
		bundle.Job.ModelVersion, bundle.Job.ConfigVersion, bundle.InputAssetIDs,
		bundle.InputURI, modelMetadata, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert pySTEPS-LK model run: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE forecast_runs
SET status = 'BASELINE_RUNNING', updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, bundle.Run.ID); err != nil {
		return fmt.Errorf("mark pySTEPS-LK baseline running: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)`,
		bundle.Outbox.ID, bundle.Outbox.AggregateID, bundle.Outbox.EventType,
		bundle.Outbox.Subject, bundle.Outbox.Payload, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert pySTEPS-LK outbox event: %w", err)
	}
	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit pySTEPS-LK transaction: %w", err)
	}
	return nil
}

func applyPystepsLKCompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
) error {
	var forecast *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		if event.Payload.Assets[index].AssetType == "forecast_output" {
			if forecast != nil {
				return fmt.Errorf("%w: multiple ForecastOutput assets", orchestration.ErrInvalidEvent)
			}
			forecast = &event.Payload.Assets[index]
		}
	}
	if forecast == nil {
		return fmt.Errorf("%w: ForecastOutput asset is required", orchestration.ErrInvalidEvent)
	}
	rawMetrics, ok := event.Payload.Diagnostics["pysteps_lk"]
	if !ok {
		return fmt.Errorf("%w: pySTEPS-LK diagnostics are required", orchestration.ErrInvalidEvent)
	}
	var metrics workflow.PystepsLKMetrics
	if err := json.Unmarshal(rawMetrics, &metrics); err != nil {
		return fmt.Errorf("%w: decode pySTEPS-LK diagnostics: %v", orchestration.ErrInvalidEvent, err)
	}
	if err := validatePystepsLKMetrics(metrics, event); err != nil {
		return err
	}

	var modelRunID uuid.UUID
	var issueTime time.Time
	var gridID, inputURI, modelID, modelVersion, configVersion, modelStatus string
	var currentStatus workflow.RunStatus
	var inputAssetIDs []uuid.UUID
	var rawRequest json.RawMessage
	if err := tx.QueryRow(ctx, `
SELECT mr.model_run_id, f.issue_time, f.grid_id, f.status,
       mr.input_uri, mr.input_asset_ids, mr.model_id, mr.model_version,
       mr.config_version, mr.status, j.request_payload
FROM model_runs AS mr
JOIN forecast_runs AS f ON f.run_id = mr.run_id
JOIN jobs AS j ON j.job_id = mr.job_id
WHERE mr.job_id = $1 AND mr.run_id = $2
FOR UPDATE OF mr, f`, event.JobID, event.RunID).Scan(
		&modelRunID, &issueTime, &gridID, &currentStatus, &inputURI,
		&inputAssetIDs, &modelID, &modelVersion, &configVersion, &modelStatus,
		&rawRequest,
	); err != nil {
		return fmt.Errorf("lock pySTEPS-LK completion: %w", err)
	}
	if currentStatus != workflow.RunBaselineRunning || modelStatus != "running" {
		return fmt.Errorf("%w: pySTEPS-LK run is not BASELINE_RUNNING", orchestration.ErrInvalidEvent)
	}
	var requested orchestration.PystepsLKRequested
	if err := json.Unmarshal(rawRequest, &requested); err != nil {
		return fmt.Errorf("decode stored pySTEPS-LK request: %w", err)
	}
	expectedForecastURI := strings.TrimRight(requested.Payload.OutputPrefix, "/") + "/forecast.zarr"
	if metrics.RunID != event.RunID || metrics.JobID != event.JobID ||
		!metrics.IssueTime.Equal(issueTime) || !requested.Payload.IssueTime.Equal(issueTime) ||
		metrics.GridID != gridID || requested.Payload.GridID != gridID ||
		metrics.InputURI != inputURI || requested.Payload.InputURI != inputURI ||
		metrics.ModelID != modelID || requested.Payload.ModelID != modelID ||
		metrics.ModelVersion != modelVersion || requested.Payload.ModelVersion != modelVersion ||
		metrics.ConfigVersion != configVersion || requested.Payload.ConfigVersion != configVersion ||
		!slices.Equal(metrics.InputAssetIDs, inputAssetIDs) ||
		!slices.Equal(requested.Payload.InputAssetIDs, inputAssetIDs) ||
		forecast.URI != expectedForecastURI {
		return fmt.Errorf("%w: pySTEPS-LK completion identity differs from request", orchestration.ErrInvalidEvent)
	}
	metrics.MeasuredAt = event.Payload.FinishedAt
	diagnostics, err := json.Marshal(metrics)
	if err != nil {
		return fmt.Errorf("encode pySTEPS-LK diagnostics: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE model_runs
SET status = 'completed', runtime_ms = $2, output_uri = $3,
    diagnostics = $4, measured_at = $5, completed_at = $5
WHERE model_run_id = $1`, modelRunID, event.Payload.RuntimeMS, forecast.URI,
		diagnostics, event.Payload.FinishedAt); err != nil {
		return fmt.Errorf("persist pySTEPS-LK completion: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE forecast_runs
SET status = 'BASELINE_READY', updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, event.RunID); err != nil {
		return fmt.Errorf("mark pySTEPS-LK baseline ready: %w", err)
	}
	ready := orchestration.ForecastBaselineReady{
		SchemaVersion: orchestration.SchemaVersion,
		EventID: uuid.NewSHA1(
			uuid.NameSpaceURL,
			[]byte("rainpulse:forecast-baseline-ready:"+event.JobID.String()),
		),
		EventType:  orchestration.ForecastBaselineReadyEventType,
		OccurredAt: event.Payload.FinishedAt,
		RunID:      event.RunID,
		JobID:      event.JobID,
		TraceID:    event.TraceID,
		Payload: orchestration.ForecastBaselineReadyPayload{
			ForecastURI: forecast.URI, IssueTime: issueTime, GridID: gridID,
			ModelID: modelID, ModelVersion: modelVersion, Config: configVersion,
			LeadCount: metrics.LeadCount, LeadStep: metrics.LeadStepMinutes,
			ValidFrom: metrics.ValidFrom, ValidTo: metrics.ValidTo,
		},
	}
	readyPayload, err := json.Marshal(ready)
	if err != nil {
		return fmt.Errorf("encode forecast baseline ready event: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)
ON CONFLICT (event_id) DO NOTHING`, ready.EventID, event.JobID.String(),
		ready.EventType, orchestration.ForecastBaselineReadySubject,
		readyPayload, ready.OccurredAt); err != nil {
		return fmt.Errorf("insert forecast baseline ready outbox event: %w", err)
	}
	return nil
}

func validatePystepsLKMetrics(
	metrics workflow.PystepsLKMetrics,
	event orchestration.JobCompleted,
) error {
	finite := func(value float64) bool { return !math.IsNaN(value) && !math.IsInf(value, 0) }
	activeMotionDiagnosticsValid := true
	if metrics.MissingPolicy == "nearest_valid_buffer_preserve_advected_mask" {
		activeMotionDiagnosticsValid = metrics.MotionFeatureCount >= 0 &&
			finite(metrics.MotionValidFraction) && metrics.MotionValidFraction >= 0 &&
			metrics.MotionValidFraction <= 1 && metrics.MissingBufferPixels > 0 &&
			metrics.ConfidenceKind == orchestration.PystepsLKConfidenceKind &&
			validPystepsFallback(metrics.MotionFallbackUsed, metrics.MotionFallbackReason)
	}
	if metrics.SchemaVersion != "1.0" || metrics.RunID == uuid.Nil ||
		metrics.JobID == uuid.Nil || metrics.IssueTime.IsZero() || metrics.GridID == "" ||
		metrics.ModelID != orchestration.PystepsLKModelID ||
		metrics.ModelVersion == "" ||
		metrics.ConfigVersion == "" || metrics.InputURI == "" ||
		len(metrics.InputAssetIDs) == 0 || metrics.LeadCount != 24 ||
		metrics.LeadStepMinutes != 5 || metrics.TrackableRainPixelCount < 0 ||
		metrics.RuntimeMS < 0 || !supportedPystepsMissingPolicy(metrics.MissingPolicy) ||
		len(metrics.BaselineModels) != 2 || metrics.BaselineModels[0] != "persistence" ||
		metrics.BaselineModels[1] != "translation" ||
		!metrics.ValidFrom.Equal(metrics.IssueTime.Add(5*time.Minute)) ||
		!metrics.ValidTo.Equal(metrics.IssueTime.Add(120*time.Minute)) ||
		!finite(metrics.FirstLeadValidCoverageRatio) ||
		!finite(metrics.LastLeadValidCoverageRatio) ||
		!finite(metrics.MaximumForecastRateMMH) ||
		metrics.FirstLeadValidCoverageRatio < 0 || metrics.FirstLeadValidCoverageRatio > 1 ||
		metrics.LastLeadValidCoverageRatio < 0 || metrics.LastLeadValidCoverageRatio > 1 ||
		metrics.MaximumForecastRateMMH < 0 || metrics.RuntimeMS > event.Payload.RuntimeMS ||
		!activeMotionDiagnosticsValid {
		return fmt.Errorf("%w: invalid pySTEPS-LK diagnostics", orchestration.ErrInvalidEvent)
	}
	return nil
}

func validPystepsFallback(used bool, reason *string) bool {
	if !used {
		return reason == nil
	}
	if reason == nil {
		return false
	}
	switch *reason {
	case "insufficient_trackable_rain", "no_motion_valid_domain", "insufficient_motion_features":
		return true
	default:
		return false
	}
}

func supportedPystepsMissingPolicy(value string) bool {
	switch value {
	case "dry_floor_working_copy_preserve_advected_mask",
		"nearest_valid_buffer_preserve_advected_mask":
		return true
	default:
		return false
	}
}

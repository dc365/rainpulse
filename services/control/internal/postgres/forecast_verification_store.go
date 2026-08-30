package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const forecastVerificationResultMediaType = "application/vnd.rainpulse.forecast-verification-result+json"

func (store *Store) GetForecastVerificationInput(
	ctx context.Context,
	runID uuid.UUID,
) (orchestration.ForecastVerificationInput, error) {
	input := orchestration.ForecastVerificationInput{
		RunID: runID, ForecastContractVersion: "1.1", ResultContractVersion: "1.0",
	}
	if err := store.pool.QueryRow(ctx, `
SELECT f.issue_time, f.grid_id, f.status, build.forecast_uri,
       build.forecast_sha256, model.model_id, model.model_version
FROM forecast_runs AS f
JOIN product_build_runs AS build
  ON build.run_id = f.run_id AND build.status = 'SUCCEEDED'
JOIN model_runs AS model ON model.model_run_id = build.model_run_id
WHERE f.run_id = $1`, runID).Scan(
		&input.IssueTime, &input.GridID, &input.CurrentStatus, &input.ForecastURI,
		&input.ForecastSHA256, &input.ModelID, &input.ModelVersion,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return orchestration.ForecastVerificationInput{}, workflow.ErrNotFound
		}
		return orchestration.ForecastVerificationInput{}, fmt.Errorf(
			"load forecast-verification input: %w", err,
		)
	}

	rows, err := store.pool.Query(ctx, `
SELECT cycle.analysis_id, cycle.analysis_time, qpe.analysis_uri, qpe.analysis_sha256
FROM analysis_cycles AS cycle
JOIN qpe_runs AS qpe ON qpe.analysis_id = cycle.analysis_id
WHERE cycle.grid_id = $1
  AND cycle.status = 'ANALYSIS_READY'
  AND qpe.status = 'SUCCEEDED'
  AND qpe.analysis_uri IS NOT NULL
  AND qpe.analysis_sha256 IS NOT NULL
  AND cycle.analysis_time > $2
  AND cycle.analysis_time <= $2 + INTERVAL '120 minutes'
ORDER BY cycle.analysis_time`, input.GridID, input.IssueTime)
	if err != nil {
		return orchestration.ForecastVerificationInput{}, fmt.Errorf(
			"list forecast-verification truth: %w", err,
		)
	}
	defer rows.Close()
	for rows.Next() {
		var frame workflow.ForecastVerificationTruth
		if err := rows.Scan(&frame.AnalysisID, &frame.ValidTime, &frame.URI, &frame.SHA256); err != nil {
			return orchestration.ForecastVerificationInput{}, fmt.Errorf(
				"scan forecast-verification truth: %w", err,
			)
		}
		input.Truth = append(input.Truth, frame)
	}
	if err := rows.Err(); err != nil {
		return orchestration.ForecastVerificationInput{}, fmt.Errorf(
			"iterate forecast-verification truth: %w", err,
		)
	}
	return input, nil
}

func (store *Store) CreateForecastVerificationBundle(
	ctx context.Context,
	bundle workflow.ForecastVerificationBundle,
) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin forecast-verification transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err = tx.Exec(ctx, `
INSERT INTO config_versions (config_version, sha256, config, description, created_at)
VALUES ($1, $2, $3, 'RP-031 automatic forecast verification', $4)
ON CONFLICT (config_version) DO NOTHING`, bundle.Job.ConfigVersion,
		bundle.VerificationConfigSHA256, bundle.VerificationConfig,
		bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert forecast-verification configuration: %w", err)
	}
	var storedHash string
	if err = tx.QueryRow(ctx, `SELECT sha256 FROM config_versions WHERE config_version = $1`,
		bundle.Job.ConfigVersion).Scan(&storedHash); err != nil {
		return fmt.Errorf("verify forecast-verification configuration: %w", err)
	}
	if storedHash != bundle.VerificationConfigSHA256 {
		return fmt.Errorf("verification profile version already has a different SHA-256")
	}

	var runStatus workflow.RunStatus
	var issueTime time.Time
	var gridID, forecastURI, forecastSHA256 string
	if err = tx.QueryRow(ctx, `
SELECT forecast.status, forecast.issue_time, forecast.grid_id,
       build.forecast_uri, build.forecast_sha256
FROM forecast_runs AS forecast
JOIN product_build_runs AS build
  ON build.run_id = forecast.run_id AND build.status = 'SUCCEEDED'
WHERE forecast.run_id = $1
FOR UPDATE OF forecast, build`, bundle.Run.ID).Scan(
		&runStatus, &issueTime, &gridID, &forecastURI, &forecastSHA256,
	); err != nil {
		return fmt.Errorf("lock forecast-verification input: %w", err)
	}
	if runStatus != workflow.RunPublished || !issueTime.Equal(bundle.Run.IssueTime) ||
		gridID != bundle.Run.GridID || forecastURI != bundle.ForecastURI ||
		forecastSHA256 != bundle.ForecastSHA256 {
		return fmt.Errorf("forecast verification input is not the committed PUBLISHED artifact")
	}
	if err := verifyCommittedTruth(ctx, tx, gridID, bundle.Truth); err != nil {
		return err
	}

	truthIDs := make([]uuid.UUID, len(bundle.Truth))
	truthTimes := make([]time.Time, len(bundle.Truth))
	truthURIs := make([]string, len(bundle.Truth))
	truthSHA256s := make([]string, len(bundle.Truth))
	for index, frame := range bundle.Truth {
		truthIDs[index] = frame.AnalysisID
		truthTimes[index] = frame.ValidTime.UTC()
		truthURIs[index] = frame.URI
		truthSHA256s[index] = frame.SHA256
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
		return fmt.Errorf("insert forecast-verification job: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO forecast_verification_runs (
    job_id, run_id, forecast_uri, forecast_sha256, forecast_contract_version,
    profile_version, result_contract_version, truth_analysis_ids,
    truth_valid_times, truth_uris, truth_sha256s, status, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'RUNNING', $12, $12)`,
		bundle.Job.ID, bundle.Run.ID, bundle.ForecastURI, bundle.ForecastSHA256,
		bundle.ForecastContractVersion, bundle.Job.ConfigVersion,
		bundle.ResultContractVersion, truthIDs, truthTimes, truthURIs,
		truthSHA256s, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert forecast-verification run: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE forecast_runs SET status = 'VERIFYING', updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, bundle.Run.ID); err != nil {
		return fmt.Errorf("mark forecast run verifying: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)`,
		bundle.Outbox.ID, bundle.Outbox.AggregateID, bundle.Outbox.EventType,
		bundle.Outbox.Subject, bundle.Outbox.Payload, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert forecast-verification outbox event: %w", err)
	}
	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit forecast-verification transaction: %w", err)
	}
	return nil
}

func verifyCommittedTruth(
	ctx context.Context,
	tx pgx.Tx,
	gridID string,
	truth []workflow.ForecastVerificationTruth,
) error {
	for index, frame := range truth {
		var validTime time.Time
		var storedURI, storedSHA256 string
		if err := tx.QueryRow(ctx, `
SELECT cycle.analysis_time, qpe.analysis_uri, qpe.analysis_sha256
FROM analysis_cycles AS cycle
JOIN qpe_runs AS qpe ON qpe.analysis_id = cycle.analysis_id
WHERE cycle.analysis_id = $1 AND cycle.grid_id = $2
  AND cycle.status = 'ANALYSIS_READY' AND qpe.status = 'SUCCEEDED'
FOR SHARE OF cycle, qpe`, frame.AnalysisID, gridID).Scan(
			&validTime, &storedURI, &storedSHA256,
		); err != nil {
			return fmt.Errorf("lock verification truth frame %d: %w", index, err)
		}
		if !validTime.Equal(frame.ValidTime) || storedURI != frame.URI ||
			storedSHA256 != frame.SHA256 {
			return fmt.Errorf("verification truth frame %d is not the committed RadarAnalysis", index)
		}
	}
	return nil
}

func (store *Store) GetForecastVerificationRecord(
	ctx context.Context,
	runID uuid.UUID,
) (workflow.ForecastVerificationRecord, error) {
	var record workflow.ForecastVerificationRecord
	if err := store.pool.QueryRow(ctx, `
SELECT job_id, run_id, status, profile_version, result_uri, result_sha256,
       COALESCE(summary, '{}'::jsonb),
       (SELECT started_at FROM jobs WHERE job_id = verification.job_id),
       measured_at, created_at, updated_at
FROM forecast_verification_runs AS verification
WHERE run_id = $1`, runID).Scan(
		&record.JobID, &record.RunID, &record.Status, &record.ProfileVersion,
		&record.ResultURI, &record.ResultSHA256, &record.Summary,
		&record.StartedAt, &record.CompletedAt, &record.CreatedAt, &record.UpdatedAt,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.ForecastVerificationRecord{}, workflow.ErrNotFound
		}
		return workflow.ForecastVerificationRecord{}, fmt.Errorf(
			"get forecast-verification record: %w", err,
		)
	}
	return record, nil
}

func (store *Store) GetForecastVerificationStatus(
	ctx context.Context,
	runID uuid.UUID,
) (workflow.ForecastVerificationStatus, error) {
	var result workflow.ForecastVerificationStatus
	result.RunID = runID
	var recordStatus *string
	var rawSummary json.RawMessage
	if err := store.pool.QueryRow(ctx, `
SELECT forecast.issue_time, forecast.status, verification.status,
       verification.profile_version, verification.result_uri,
       verification.result_sha256, COALESCE(verification.summary, '{}'::jsonb),
       verification.measured_at
FROM forecast_runs AS forecast
LEFT JOIN forecast_verification_runs AS verification
  ON verification.run_id = forecast.run_id
WHERE forecast.run_id = $1`, runID).Scan(
		&result.IssueTime, &result.RunStatus, &recordStatus, &result.ProfileVersion,
		&result.ResultURI, &result.ResultSHA256, &rawSummary, &result.VerifiedAt,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.ForecastVerificationStatus{}, workflow.ErrNotFound
		}
		return workflow.ForecastVerificationStatus{}, fmt.Errorf(
			"get forecast-verification status: %w", err,
		)
	}
	rows, err := store.pool.Query(ctx, `
SELECT lead.value,
       EXISTS (
           SELECT 1
           FROM analysis_cycles AS cycle
           JOIN qpe_runs AS qpe ON qpe.analysis_id = cycle.analysis_id
           WHERE cycle.grid_id = forecast.grid_id
             AND cycle.analysis_time = forecast.issue_time + lead.value * INTERVAL '5 minutes'
             AND cycle.status = 'ANALYSIS_READY'
             AND qpe.status = 'SUCCEEDED'
             AND qpe.analysis_uri IS NOT NULL
             AND qpe.analysis_sha256 IS NOT NULL
       )
FROM forecast_runs AS forecast
CROSS JOIN generate_series(1, 24) AS lead(value)
WHERE forecast.run_id = $1
ORDER BY lead.value`, runID)
	if err != nil {
		return workflow.ForecastVerificationStatus{}, fmt.Errorf(
			"count forecast-verification truth: %w", err,
		)
	}
	defer rows.Close()
	for rows.Next() {
		var leadIndex int
		var exists bool
		if err := rows.Scan(&leadIndex, &exists); err != nil {
			return workflow.ForecastVerificationStatus{}, fmt.Errorf(
				"scan forecast-verification truth status: %w", err,
			)
		}
		if exists {
			result.TruthFrameCount++
		} else {
			result.MissingLeadMinutes = append(result.MissingLeadMinutes, leadIndex*5)
		}
	}
	if err := rows.Err(); err != nil {
		return workflow.ForecastVerificationStatus{}, fmt.Errorf(
			"iterate forecast-verification truth status: %w", err,
		)
	}
	switch {
	case recordStatus == nil:
		if result.RunStatus == workflow.RunPublished {
			result.Status = "waiting_truth"
		} else {
			result.Status = "pending_forecast"
		}
	case *recordStatus == "RUNNING":
		result.Status = "running"
	case *recordStatus == "SUCCEEDED":
		result.Status = "succeeded"
	case *recordStatus == "FAILED":
		result.Status = "failed"
	default:
		return workflow.ForecastVerificationStatus{}, fmt.Errorf(
			"unsupported forecast-verification record status %q", *recordStatus,
		)
	}
	if len(rawSummary) > 0 && string(rawSummary) != "{}" {
		var summary workflow.ForecastVerificationResultSummary
		if err := json.Unmarshal(rawSummary, &summary); err != nil {
			return workflow.ForecastVerificationStatus{}, fmt.Errorf(
				"decode forecast-verification summary: %w", err,
			)
		}
		result.Summary = &summary
	}
	return result, nil
}

func applyForecastVerificationCompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
) error {
	var resultAsset *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		asset := &event.Payload.Assets[index]
		if asset.AssetType == "forecast_verification_result" {
			if resultAsset != nil {
				return fmt.Errorf("%w: multiple verification result assets", orchestration.ErrInvalidEvent)
			}
			resultAsset = asset
		}
	}
	if resultAsset == nil || resultAsset.MediaType != forecastVerificationResultMediaType {
		return fmt.Errorf("%w: forecast verification result is required", orchestration.ErrInvalidEvent)
	}
	resultDataURI, err := artifactDataURI(*resultAsset, event.Payload.Diagnostics)
	if err != nil {
		return fmt.Errorf("%w: %v", orchestration.ErrInvalidEvent, err)
	}
	rawSummary, exists := event.Payload.Diagnostics["forecast_verification"]
	if !exists {
		return fmt.Errorf("%w: forecast verification summary is required", orchestration.ErrInvalidEvent)
	}
	var summary workflow.ForecastVerificationResultSummary
	if err := json.Unmarshal(rawSummary, &summary); err != nil {
		return fmt.Errorf("%w: decode forecast verification summary: %v", orchestration.ErrInvalidEvent, err)
	}

	var record workflow.ForecastVerificationRecord
	var runStatus workflow.RunStatus
	var issueTime time.Time
	var gridID, forecastURI, forecastSHA256, modelID, modelVersion string
	var forecastContract string
	var truthIDs []uuid.UUID
	var truthURIs []string
	var requestJSON json.RawMessage
	if err := tx.QueryRow(ctx, `
SELECT verification.job_id, verification.run_id, verification.status,
       verification.profile_version, verification.result_uri,
       verification.result_sha256, COALESCE(verification.summary, '{}'::jsonb),
       verification.created_at, verification.updated_at,
       forecast.status, forecast.issue_time, forecast.grid_id,
       verification.forecast_uri, verification.forecast_sha256,
       verification.forecast_contract_version,
       verification.truth_analysis_ids, verification.truth_uris,
       job.model_id, job.model_version, job.request_payload
FROM forecast_verification_runs AS verification
JOIN forecast_runs AS forecast ON forecast.run_id = verification.run_id
JOIN jobs AS job ON job.job_id = verification.job_id
WHERE verification.job_id = $1 AND verification.run_id = $2
FOR UPDATE OF verification, forecast`, event.JobID, event.RunID).Scan(
		&record.JobID, &record.RunID, &record.Status, &record.ProfileVersion,
		&record.ResultURI, &record.ResultSHA256, &record.Summary,
		&record.CreatedAt, &record.UpdatedAt, &runStatus, &issueTime, &gridID,
		&forecastURI, &forecastSHA256, &forecastContract, &truthIDs, &truthURIs,
		&modelID, &modelVersion, &requestJSON,
	); err != nil {
		return fmt.Errorf("lock forecast-verification completion: %w", err)
	}
	if record.Status != "RUNNING" || runStatus != workflow.RunVerifying {
		return fmt.Errorf("%w: forecast verification is not running", orchestration.ErrInvalidEvent)
	}
	var requested orchestration.ForecastVerificationRequested
	if err := json.Unmarshal(requestJSON, &requested); err != nil {
		return fmt.Errorf("decode stored forecast-verification request: %w", err)
	}
	expectedAssetURI := strings.TrimRight(requested.Payload.OutputPrefix, "/") +
		"/verification-result"
	if resultAsset.URI != expectedAssetURI || summary.ContractName != "rainpulse.forecast-verification-result" ||
		summary.ContractVersion != "1.0" || summary.ProfileVersion != record.ProfileVersion ||
		summary.RunID != event.RunID || summary.JobID != event.JobID ||
		summary.ForecastURI != forecastURI || summary.ForecastContractVersion != forecastContract ||
		summary.ModelID != modelID || summary.ModelVersion != modelVersion ||
		!summary.IssueTime.Equal(issueTime) || summary.GridID != gridID ||
		summary.TruthKind != "radar_analysis_rate_qpe" || summary.TruthContractVersion != "1.2" ||
		summary.TruthFrameCount != 24 || !slices.Equal(summary.TruthAnalysisIDs, truthIDs) ||
		!slices.Equal(summary.TruthURIs, truthURIs) || summary.LeadCount != 24 ||
		!slices.Equal(summary.LeadMinutes, verificationLeadMinutes()) ||
		!slices.Equal(summary.Models, []string{"lk", "persistence", "translation"}) ||
		summary.MetricRowCount != 2160 || summary.AccumulationMetricRowCount != 150 ||
		summary.NominalPixelSpacingKM <= 0 || summary.ValidityDomain != "common" ||
		summary.PromotionEligible || summary.Headline.Band != "5-60_minutes" ||
		summary.Headline.ThresholdMMH != 5 || summary.Headline.FSSWindowTargetKM != 10 ||
		forecastSHA256 != requested.Payload.ForecastSHA256 {
		return fmt.Errorf("%w: verification completion identity differs from request", orchestration.ErrInvalidEvent)
	}
	if _, err = tx.Exec(ctx, `
UPDATE forecast_verification_runs
SET status = 'SUCCEEDED', result_uri = $2, result_sha256 = $3,
    result_size_bytes = $4, summary = $5, measured_at = $6,
    updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1`, event.JobID, resultDataURI, resultAsset.SHA256,
		resultAsset.SizeBytes, rawSummary, event.Payload.FinishedAt); err != nil {
		return fmt.Errorf("persist forecast-verification completion: %w", err)
	}
	return nil
}

func verificationLeadMinutes() []int {
	leads := make([]int, 24)
	for index := range leads {
		leads[index] = (index + 1) * 5
	}
	return leads
}

package postgres

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func (store *Store) CreateRadarDecodeBundle(ctx context.Context, bundle workflow.RadarDecodeBundle) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin radar decode transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err = tx.Exec(ctx, `
INSERT INTO config_versions (config_version, sha256, config, description, created_at)
VALUES ($1, $2, $3, 'Radar configuration registered by RP-007 decode workflow', $4)
ON CONFLICT (config_version) DO NOTHING`,
		bundle.Radar.ConfigVersion, bundle.ConfigSHA256, bundle.Config, bundle.Radar.CreatedAt); err != nil {
		return fmt.Errorf("insert global radar config %s: %w", bundle.Radar.ConfigVersion, err)
	}
	var storedConfigHash string
	if err = tx.QueryRow(ctx, `SELECT sha256 FROM config_versions WHERE config_version = $1`,
		bundle.Radar.ConfigVersion).Scan(&storedConfigHash); err != nil {
		return fmt.Errorf("verify global radar config %s: %w", bundle.Radar.ConfigVersion, err)
	}
	if storedConfigHash != bundle.ConfigSHA256 {
		return fmt.Errorf("radar config version %s already has a different SHA-256", bundle.Radar.ConfigVersion)
	}

	_, err = tx.Exec(ctx, `
INSERT INTO data_sources (
    source_id, name, source_type, enabled, config_version, metadata, created_at, updated_at
) VALUES ($1, $2, 'radar', TRUE, $3, $4, $5, $5)
ON CONFLICT (source_id) DO UPDATE SET
    config_version = EXCLUDED.config_version,
    metadata = EXCLUDED.metadata,
    updated_at = EXCLUDED.updated_at`,
		bundle.Asset.SourceID, "radar:"+bundle.Radar.ID, bundle.Radar.ConfigVersion,
		json.RawMessage(fmt.Sprintf(`{"radar_id":%q}`, bundle.Radar.ID)), bundle.Radar.CreatedAt)
	if err != nil {
		return fmt.Errorf("upsert radar source %s: %w", bundle.Radar.ID, err)
	}

	_, err = tx.Exec(ctx, `
INSERT INTO input_assets (
    asset_id, source_id, issue_time, observed_at, object_uri, media_type,
    size_bytes, sha256, status, quality_state, metadata, created_at
) VALUES ($1, $2, $3, $3, $4, $5, $6, $7, 'available', 'unknown', $8, $9)
ON CONFLICT (asset_id) DO NOTHING`,
		bundle.Asset.ID, bundle.Asset.SourceID, bundle.Asset.ObservedAt,
		bundle.Asset.ObjectURI, bundle.Asset.MediaType, bundle.Asset.SizeBytes,
		bundle.Asset.SHA256, bundle.Asset.Metadata, bundle.Radar.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert raw radar asset: %w", err)
	}
	var storedAssetHash string
	if err = tx.QueryRow(ctx, `SELECT sha256 FROM input_assets WHERE asset_id = $1`,
		bundle.Asset.ID).Scan(&storedAssetHash); err != nil {
		return fmt.Errorf("verify raw radar asset: %w", err)
	}
	if storedAssetHash != bundle.Asset.SHA256 {
		return fmt.Errorf("raw radar asset identity already refers to different content")
	}

	_, err = tx.Exec(ctx, `
INSERT INTO radars (
    radar_id, display_name, lifecycle, current_config_version, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $5)
ON CONFLICT (radar_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    lifecycle = EXCLUDED.lifecycle,
    current_config_version = EXCLUDED.current_config_version,
    updated_at = EXCLUDED.updated_at`,
		bundle.Radar.ID, bundle.Radar.DisplayName, bundle.Radar.Lifecycle,
		bundle.Radar.ConfigVersion, bundle.Radar.CreatedAt)
	if err != nil {
		return fmt.Errorf("upsert radar %s: %w", bundle.Radar.ID, err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO radar_config_versions (
    radar_id, radar_config_version, config, sha256, created_at
) VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (radar_id, radar_config_version) DO NOTHING`,
		bundle.Radar.ID, bundle.Radar.ConfigVersion, bundle.Config,
		bundle.ConfigSHA256, bundle.Radar.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert radar config %s: %w", bundle.Radar.ID, err)
	}
	var storedRadarConfigHash string
	if err = tx.QueryRow(ctx, `
SELECT sha256 FROM radar_config_versions
WHERE radar_id = $1 AND radar_config_version = $2`,
		bundle.Radar.ID, bundle.Radar.ConfigVersion).Scan(&storedRadarConfigHash); err != nil {
		return fmt.Errorf("verify radar config %s: %w", bundle.Radar.ID, err)
	}
	if storedRadarConfigHash != bundle.ConfigSHA256 {
		return fmt.Errorf("radar %s config version %s already has a different SHA-256", bundle.Radar.ID, bundle.Radar.ConfigVersion)
	}

	_, err = tx.Exec(ctx, `
INSERT INTO workflow_runs (run_id, run_type, created_at)
VALUES ($1, 'radar_scan', $2)
ON CONFLICT (run_id) DO NOTHING`, bundle.Scan.RunID, bundle.Scan.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert radar scan workflow identity: %w", err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO radar_scans (
    scan_id, radar_id, raw_asset_id, volume_start_time, volume_end_time, received_at, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (scan_id) DO NOTHING`,
		bundle.Scan.ID, bundle.Scan.RadarID, bundle.Asset.ID,
		bundle.Scan.VolumeStartTime, bundle.Scan.VolumeEndTime,
		bundle.Scan.ReceivedAt, bundle.Scan.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert radar scan %s: %w", bundle.Scan.ID, err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO radar_scan_runs (
    run_id, scan_id, radar_id, radar_config_version, status, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $6)
ON CONFLICT (run_id) DO NOTHING`,
		bundle.Scan.RunID, bundle.Scan.ID, bundle.Scan.RadarID,
		bundle.Scan.RadarConfigVersion, bundle.Scan.Status, bundle.Scan.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert radar scan run %s: %w", bundle.Scan.RunID, err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO jobs (
    job_id, run_id, trace_id, job_type, model_id, model_version,
    config_version, status, max_attempts, scheduled_at, created_at, updated_at,
    request_payload
) VALUES ($1, $2, $3, $4, NULL, NULL, $5, $6, 3, $7, $7, $7, $8)
ON CONFLICT (job_id) DO NOTHING`,
		bundle.Job.ID, bundle.Job.RunID, bundle.Job.TraceID, bundle.Job.JobType,
		bundle.Job.ConfigVersion, bundle.Job.Status, bundle.Job.CreatedAt,
		bundle.Job.RequestPayload)
	if err != nil {
		return fmt.Errorf("insert radar decode job: %w", err)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)
ON CONFLICT (event_id) DO NOTHING`,
		bundle.Outbox.ID, bundle.Outbox.AggregateID, bundle.Outbox.EventType,
		bundle.Outbox.Subject, bundle.Outbox.Payload, bundle.Job.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert radar decode outbox event: %w", err)
	}

	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit radar decode transaction: %w", err)
	}
	return nil
}

func (store *Store) CreateRadarQCBundle(ctx context.Context, bundle workflow.RadarQCBundle) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin radar QC transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var scanID uuid.UUID
	var status workflow.RadarScanStatus
	var normalizedURI string
	if err = tx.QueryRow(ctx, `
SELECT scan_id, status, COALESCE(normalized_uri, '') FROM radar_scan_runs
WHERE run_id = $1 FOR UPDATE`, bundle.Job.RunID).Scan(&scanID, &status, &normalizedURI); err != nil {
		return fmt.Errorf("lock radar scan for QC: %w", err)
	}
	if scanID != bundle.ScanID {
		return fmt.Errorf("radar QC scan identity differs from its run")
	}
	if status != workflow.RadarScanNormalized && status != workflow.RadarScanQCRunning &&
		status != workflow.RadarScanQCReady && status != workflow.RadarScanFailed {
		return fmt.Errorf("radar scan status %s cannot create a QC job", status)
	}
	if normalizedURI == "" {
		return fmt.Errorf("radar scan %s has no normalized artifact for QC", bundle.ScanID)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO config_versions (config_version, sha256, config, description, created_at)
VALUES ($1, $2, $3, 'Radar QC configuration registered by RP-008 workflow', $4)
ON CONFLICT (config_version) DO NOTHING`,
		bundle.Job.ConfigVersion, bundle.ConfigSHA256, bundle.Config, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert radar QC config %s: %w", bundle.Job.ConfigVersion, err)
	}
	var storedConfigHash string
	if err = tx.QueryRow(ctx, `SELECT sha256 FROM config_versions WHERE config_version = $1`,
		bundle.Job.ConfigVersion).Scan(&storedConfigHash); err != nil {
		return fmt.Errorf("verify radar QC config %s: %w", bundle.Job.ConfigVersion, err)
	}
	if storedConfigHash != bundle.ConfigSHA256 {
		return fmt.Errorf("radar QC config version %s already has a different SHA-256", bundle.Job.ConfigVersion)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO jobs (
    job_id, run_id, trace_id, job_type, model_id, model_version,
    config_version, status, max_attempts, scheduled_at, created_at, updated_at,
    request_payload
) VALUES ($1, $2, $3, $4, NULL, NULL, $5, $6, 3, $7, $7, $7, $8)
ON CONFLICT (job_id) DO NOTHING`,
		bundle.Job.ID, bundle.Job.RunID, bundle.Job.TraceID, bundle.Job.JobType,
		bundle.Job.ConfigVersion, bundle.Job.Status, bundle.Job.CreatedAt,
		bundle.Job.RequestPayload)
	if err != nil {
		return fmt.Errorf("insert radar QC job: %w", err)
	}
	outboxResult, err := tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)
ON CONFLICT (event_id) DO NOTHING`,
		bundle.Outbox.ID, bundle.Outbox.AggregateID, bundle.Outbox.EventType,
		bundle.Outbox.Subject, bundle.Outbox.Payload, bundle.Job.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert radar QC outbox event: %w", err)
	}
	createdOutbox := outboxResult.RowsAffected() == 1
	if status == workflow.RadarScanFailed && !createdOutbox {
		return fmt.Errorf("radar QC retry requires a new pipeline version after failure")
	}
	_, err = tx.Exec(ctx, `
UPDATE radar_scan_runs
SET status = CASE
        WHEN $2 AND status IN ('NORMALIZED', 'FAILED', 'QC_READY') THEN 'QC_RUNNING'
        ELSE status
    END,
    degraded_reason = CASE WHEN $2 AND status = 'FAILED' THEN NULL ELSE degraded_reason END,
    updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, bundle.Job.RunID, createdOutbox)
	if err != nil {
		return fmt.Errorf("mark radar scan QC running: %w", err)
	}
	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit radar QC transaction: %w", err)
	}
	return nil
}

func (store *Store) CreateRadarGridBundle(ctx context.Context, bundle workflow.RadarGridBundle) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin radar grid transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var scanID uuid.UUID
	var status workflow.RadarScanStatus
	var qcURI string
	if err = tx.QueryRow(ctx, `
SELECT scan_id, status, COALESCE(qc_uri, '') FROM radar_scan_runs
WHERE run_id = $1 FOR UPDATE`, bundle.Job.RunID).Scan(&scanID, &status, &qcURI); err != nil {
		return fmt.Errorf("lock radar scan for gridding: %w", err)
	}
	if scanID != bundle.ScanID {
		return fmt.Errorf("radar grid scan identity differs from its run")
	}
	if status != workflow.RadarScanQCReady && status != workflow.RadarScanGridRunning &&
		status != workflow.RadarScanGridReady && status != workflow.RadarScanFailed {
		return fmt.Errorf("radar scan status %s cannot create a grid job", status)
	}
	if qcURI == "" {
		return fmt.Errorf("radar scan %s has no QC artifact for gridding", bundle.ScanID)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO config_versions (config_version, sha256, config, description, created_at)
VALUES ($1, $2, $3, 'Radar grid configuration registered by RP-009 workflow', $4)
ON CONFLICT (config_version) DO NOTHING`,
		bundle.Job.ConfigVersion, bundle.ConfigSHA256, bundle.Config, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert radar grid config %s: %w", bundle.Job.ConfigVersion, err)
	}
	var storedConfigHash string
	if err = tx.QueryRow(ctx, `SELECT sha256 FROM config_versions WHERE config_version = $1`,
		bundle.Job.ConfigVersion).Scan(&storedConfigHash); err != nil {
		return fmt.Errorf("verify radar grid config %s: %w", bundle.Job.ConfigVersion, err)
	}
	if storedConfigHash != bundle.ConfigSHA256 {
		return fmt.Errorf(
			"radar grid config version %s already has a different SHA-256",
			bundle.Job.ConfigVersion,
		)
	}
	_, err = tx.Exec(ctx, `
INSERT INTO jobs (
    job_id, run_id, trace_id, job_type, model_id, model_version,
    config_version, status, max_attempts, scheduled_at, created_at, updated_at,
    request_payload
) VALUES ($1, $2, $3, $4, NULL, NULL, $5, $6, 3, $7, $7, $7, $8)
ON CONFLICT (job_id) DO NOTHING`,
		bundle.Job.ID, bundle.Job.RunID, bundle.Job.TraceID, bundle.Job.JobType,
		bundle.Job.ConfigVersion, bundle.Job.Status, bundle.Job.CreatedAt,
		bundle.Job.RequestPayload)
	if err != nil {
		return fmt.Errorf("insert radar grid job: %w", err)
	}
	outboxResult, err := tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)
ON CONFLICT (event_id) DO NOTHING`,
		bundle.Outbox.ID, bundle.Outbox.AggregateID, bundle.Outbox.EventType,
		bundle.Outbox.Subject, bundle.Outbox.Payload, bundle.Job.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert radar grid outbox event: %w", err)
	}
	createdOutbox := outboxResult.RowsAffected() == 1
	if status == workflow.RadarScanFailed && !createdOutbox {
		return fmt.Errorf("radar grid retry requires a new Hybrid Scan version after failure")
	}
	_, err = tx.Exec(ctx, `
UPDATE radar_scan_runs
SET status = CASE
        WHEN $2 AND status IN ('QC_READY', 'FAILED', 'RADAR_GRID_READY') THEN 'GRID_RUNNING'
        ELSE status
    END,
    degraded_reason = CASE WHEN $2 AND status = 'FAILED' THEN NULL ELSE degraded_reason END,
    updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, bundle.Job.RunID, createdOutbox)
	if err != nil {
		return fmt.Errorf("mark radar scan grid running: %w", err)
	}
	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit radar grid transaction: %w", err)
	}
	return nil
}

func (store *Store) CreateDomainSimulation(ctx context.Context, simulation workflow.DomainSimulation) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin domain simulation transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	for _, radar := range simulation.Radars {
		_, err = tx.Exec(ctx, `
INSERT INTO radars (
    radar_id, display_name, lifecycle, current_config_version, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $5)
ON CONFLICT (radar_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    lifecycle = EXCLUDED.lifecycle,
    current_config_version = EXCLUDED.current_config_version,
    updated_at = EXCLUDED.updated_at`,
			radar.ID, radar.DisplayName, radar.Lifecycle, radar.ConfigVersion, radar.CreatedAt)
		if err != nil {
			return fmt.Errorf("upsert simulated radar %s: %w", radar.ID, err)
		}

		configHash := fmt.Sprintf("%x", sha256.Sum256([]byte(radar.ConfigVersion)))
		_, err = tx.Exec(ctx, `
INSERT INTO radar_config_versions (
    radar_id, radar_config_version, config, sha256, created_at
) VALUES ($1, $2, '{"simulation":true}'::jsonb, $3, $4)
ON CONFLICT (radar_id, radar_config_version) DO NOTHING`,
			radar.ID, radar.ConfigVersion, configHash, radar.CreatedAt)
		if err != nil {
			return fmt.Errorf("insert simulated radar config %s: %w", radar.ID, err)
		}
	}

	for _, scan := range simulation.Scans {
		if err := insertWorkflowRun(ctx, tx, scan.RunID, workflow.WorkflowRadarScan, scan.CreatedAt); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `
INSERT INTO radar_scans (
    scan_id, radar_id, volume_start_time, volume_end_time, received_at, created_at
) VALUES ($1, $2, $3, $4, $5, $6)`,
			scan.ID, scan.RadarID, scan.VolumeStartTime, scan.VolumeEndTime,
			scan.ReceivedAt, scan.CreatedAt)
		if err != nil {
			return fmt.Errorf("insert simulated radar scan %s: %w", scan.ID, err)
		}
		_, err = tx.Exec(ctx, `
INSERT INTO radar_scan_runs (
    run_id, scan_id, radar_id, radar_config_version, status, degraded_reason,
    normalized_uri, qc_uri, grid_uri, scan_completeness, mean_quality_index,
    created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12)`,
			scan.RunID, scan.ID, scan.RadarID, scan.RadarConfigVersion, scan.Status,
			scan.DegradedReason, scan.NormalizedURI, scan.QCURI, scan.GridURI,
			scan.ScanCompleteness, scan.MeanQualityIndex, scan.CreatedAt)
		if err != nil {
			return fmt.Errorf("insert simulated radar scan run %s: %w", scan.RunID, err)
		}
	}

	analysis := simulation.Analysis
	if err := insertWorkflowRun(ctx, tx, analysis.RunID, workflow.WorkflowAnalysisCycle, analysis.CreatedAt); err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
INSERT INTO analysis_cycles (
    analysis_id, run_id, analysis_time, grid_id, config_version, status,
    degraded_reason, radar_count, valid_coverage_ratio, mean_quality_index,
    analysis_uri, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12)`,
		analysis.ID, analysis.RunID, analysis.AnalysisTime, analysis.GridID,
		analysis.ConfigVersion, analysis.Status, analysis.DegradedReason,
		analysis.RadarCount, analysis.ValidCoverageRatio, analysis.MeanQualityIndex,
		analysis.AnalysisURI, analysis.CreatedAt)
	if err != nil {
		return fmt.Errorf("insert simulated analysis cycle %s: %w", analysis.ID, err)
	}
	for _, radar := range analysis.Radars {
		_, err = tx.Exec(ctx, `
INSERT INTO analysis_cycle_radars (
    analysis_id, radar_id, scan_id, state, time_offset_seconds,
    mean_quality_index, exclusion_reason, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
			analysis.ID, radar.RadarID, radar.ScanID, radar.State,
			radar.TimeOffsetSeconds, radar.MeanQualityIndex,
			radar.ExclusionReason, analysis.CreatedAt)
		if err != nil {
			return fmt.Errorf("insert simulated analysis radar %s: %w", radar.RadarID, err)
		}
	}

	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit domain simulation transaction: %w", err)
	}
	return nil
}

func insertWorkflowRun(
	ctx context.Context,
	tx pgx.Tx,
	runID uuid.UUID,
	runType workflow.WorkflowType,
	createdAt time.Time,
) error {
	_, err := tx.Exec(ctx, `
INSERT INTO workflow_runs (run_id, run_type, created_at)
VALUES ($1, $2, $3)`, runID, runType, createdAt)
	if err != nil {
		return fmt.Errorf("insert %s workflow identity: %w", runType, err)
	}
	return nil
}

func (store *Store) ListRadars(ctx context.Context) ([]workflow.Radar, error) {
	rows, err := store.pool.Query(ctx, radarSelect+` ORDER BY r.radar_id`)
	if err != nil {
		return nil, fmt.Errorf("list radars: %w", err)
	}
	defer rows.Close()

	radars := make([]workflow.Radar, 0)
	for rows.Next() {
		radar, err := scanRadar(rows)
		if err != nil {
			return nil, err
		}
		radars = append(radars, radar)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate radars: %w", err)
	}
	return radars, nil
}

func (store *Store) GetRadar(ctx context.Context, radarID string) (workflow.Radar, error) {
	return scanRadar(store.pool.QueryRow(ctx, radarSelect+` WHERE r.radar_id = $1`, radarID))
}

func (store *Store) GetRadarStatus(ctx context.Context, radarID string) (workflow.RadarStatusSummary, error) {
	radar, err := store.GetRadar(ctx, radarID)
	if err != nil {
		return workflow.RadarStatusSummary{}, err
	}
	row := store.pool.QueryRow(ctx, `
SELECT s.scan_id, s.volume_end_time, r.status, r.scan_completeness,
       r.mean_quality_index,
       GREATEST(0, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - s.volume_end_time)))::bigint,
       EXISTS (
           SELECT 1
           FROM analysis_cycle_radars AS acr
           WHERE acr.radar_id = $1
             AND acr.state = 'PARTICIPATING'
             AND acr.analysis_id = (
                 SELECT analysis_id FROM analysis_cycles
                 ORDER BY analysis_time DESC, created_at DESC LIMIT 1
             )
       ), h.health_state, COALESCE(h.diagnostics, 'null'::jsonb),
       COALESCE(q.diagnostics, 'null'::jsonb)
FROM radar_scans AS s
JOIN radar_scan_runs AS r ON r.scan_id = s.scan_id
LEFT JOIN radar_health_metrics AS h ON h.scan_id = s.scan_id
LEFT JOIN radar_qc_metrics AS q ON q.scan_id = s.scan_id
WHERE s.radar_id = $1
ORDER BY s.volume_end_time DESC, r.created_at DESC
LIMIT 1`, radarID)

	var summary workflow.RadarStatusSummary
	summary.RadarID = radarID
	summary.DisplayName = radar.DisplayName
	summary.Lifecycle = radar.Lifecycle
	summary.ConfigVersion = radar.ConfigVersion
	var status workflow.RadarScanStatus
	var healthState *string
	var rawHealth json.RawMessage
	var rawQC json.RawMessage
	if err := row.Scan(
		&summary.LatestScanID, &summary.LatestScanTime, &status,
		&summary.ScanCompleteness, &summary.MeanQualityIndex,
		&summary.DataDelaySeconds, &summary.ParticipatingInLatestAnalysis,
		&healthState, &rawHealth, &rawQC,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			summary.Health = workflow.RadarHealthUnknown
			return summary, nil
		}
		return workflow.RadarStatusSummary{}, fmt.Errorf("get radar status: %w", err)
	}
	summary.ScanStatus = &status
	if healthState != nil {
		summary.Health = workflow.RadarHealthState(*healthState)
		var metrics workflow.RadarHealthMetrics
		if err := json.Unmarshal(rawHealth, &metrics); err != nil {
			return workflow.RadarStatusSummary{}, fmt.Errorf("decode radar health metrics: %w", err)
		}
		summary.HealthMetrics = &metrics
	}
	if string(rawQC) != "null" {
		var metrics workflow.RadarQCMetrics
		if err := json.Unmarshal(rawQC, &metrics); err != nil {
			return workflow.RadarStatusSummary{}, fmt.Errorf("decode radar QC metrics: %w", err)
		}
		summary.QCMetrics = &metrics
	}
	if healthState != nil {
		return summary, nil
	}
	switch status {
	case workflow.RadarScanGridReady:
		summary.Health = workflow.RadarHealthHealthy
	case workflow.RadarScanFailed:
		summary.Health = workflow.RadarHealthUnavailable
	case workflow.RadarScanDegraded:
		summary.Health = workflow.RadarHealthDegraded
	default:
		summary.Health = workflow.RadarHealthUnknown
	}
	return summary, nil
}

func (store *Store) ListRadarStatuses(ctx context.Context) ([]workflow.RadarStatusSummary, error) {
	radars, err := store.ListRadars(ctx)
	if err != nil {
		return nil, err
	}
	statuses := make([]workflow.RadarStatusSummary, 0, len(radars))
	for _, radar := range radars {
		status, err := store.GetRadarStatus(ctx, radar.ID)
		if err != nil {
			return nil, err
		}
		statuses = append(statuses, status)
	}
	return statuses, nil
}

func (store *Store) ListRadarScans(
	ctx context.Context,
	limit int,
	radarID *string,
	status *workflow.RadarScanStatus,
) ([]workflow.RadarScan, error) {
	radarValue := ""
	if radarID != nil {
		radarValue = *radarID
	}
	statusValue := ""
	if status != nil {
		statusValue = string(*status)
	}
	rows, err := store.pool.Query(ctx, radarScanSelect+`
WHERE ($1 = '' OR s.radar_id = $1)
  AND ($2 = '' OR r.status = $2)
ORDER BY s.volume_start_time DESC, r.created_at DESC
LIMIT $3`, radarValue, statusValue, limit)
	if err != nil {
		return nil, fmt.Errorf("list radar scans: %w", err)
	}
	defer rows.Close()

	scans := make([]workflow.RadarScan, 0)
	for rows.Next() {
		scan, err := scanRadarScan(rows)
		if err != nil {
			return nil, err
		}
		scans = append(scans, scan)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate radar scans: %w", err)
	}
	return scans, nil
}

func (store *Store) GetRadarScan(ctx context.Context, scanID uuid.UUID) (workflow.RadarScan, error) {
	return scanRadarScan(store.pool.QueryRow(ctx, radarScanSelect+` WHERE s.scan_id = $1`, scanID))
}

func (store *Store) GetRadarHealthMetrics(
	ctx context.Context,
	scanID uuid.UUID,
) (workflow.RadarHealthMetrics, error) {
	var raw json.RawMessage
	if err := store.pool.QueryRow(ctx, `
SELECT diagnostics FROM radar_health_metrics WHERE scan_id = $1`, scanID).Scan(&raw); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.RadarHealthMetrics{}, workflow.ErrNotFound
		}
		return workflow.RadarHealthMetrics{}, fmt.Errorf("get radar health metrics: %w", err)
	}
	var metrics workflow.RadarHealthMetrics
	if err := json.Unmarshal(raw, &metrics); err != nil {
		return workflow.RadarHealthMetrics{}, fmt.Errorf("decode radar health metrics: %w", err)
	}
	return metrics, nil
}

func (store *Store) GetRadarQCMetrics(
	ctx context.Context,
	scanID uuid.UUID,
) (workflow.RadarQCMetrics, error) {
	var raw json.RawMessage
	if err := store.pool.QueryRow(ctx, `
SELECT diagnostics FROM radar_qc_metrics WHERE scan_id = $1`, scanID).Scan(&raw); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.RadarQCMetrics{}, workflow.ErrNotFound
		}
		return workflow.RadarQCMetrics{}, fmt.Errorf("get radar QC metrics: %w", err)
	}
	var metrics workflow.RadarQCMetrics
	if err := json.Unmarshal(raw, &metrics); err != nil {
		return workflow.RadarQCMetrics{}, fmt.Errorf("decode radar QC metrics: %w", err)
	}
	return metrics, nil
}

func (store *Store) GetRadarGridMetrics(
	ctx context.Context,
	scanID uuid.UUID,
) (workflow.RadarGridMetrics, error) {
	var raw json.RawMessage
	if err := store.pool.QueryRow(ctx, `
SELECT diagnostics FROM radar_grid_metrics WHERE scan_id = $1`, scanID).Scan(&raw); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.RadarGridMetrics{}, workflow.ErrNotFound
		}
		return workflow.RadarGridMetrics{}, fmt.Errorf("get radar grid metrics: %w", err)
	}
	var metrics workflow.RadarGridMetrics
	if err := json.Unmarshal(raw, &metrics); err != nil {
		return workflow.RadarGridMetrics{}, fmt.Errorf("decode radar grid metrics: %w", err)
	}
	return metrics, nil
}

func (store *Store) ListAnalysisCycles(
	ctx context.Context,
	limit int,
	status *workflow.AnalysisStatus,
) ([]workflow.AnalysisCycle, error) {
	statusValue := ""
	if status != nil {
		statusValue = string(*status)
	}
	rows, err := store.pool.Query(ctx, analysisSelect+`
WHERE ($1 = '' OR a.status = $1)
ORDER BY a.analysis_time DESC, a.created_at DESC
LIMIT $2`, statusValue, limit)
	if err != nil {
		return nil, fmt.Errorf("list analysis cycles: %w", err)
	}
	defer rows.Close()

	cycles := make([]workflow.AnalysisCycle, 0)
	for rows.Next() {
		cycle, err := scanAnalysis(rows)
		if err != nil {
			return nil, err
		}
		cycles = append(cycles, cycle)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate analysis cycles: %w", err)
	}
	rows.Close()
	for index := range cycles {
		cycles[index].Radars, err = store.listAnalysisRadars(ctx, cycles[index].ID)
		if err != nil {
			return nil, err
		}
	}
	return cycles, nil
}

func (store *Store) GetAnalysisCycle(ctx context.Context, analysisID uuid.UUID) (workflow.AnalysisCycle, error) {
	cycle, err := scanAnalysis(store.pool.QueryRow(ctx, analysisSelect+` WHERE a.analysis_id = $1`, analysisID))
	if err != nil {
		return workflow.AnalysisCycle{}, err
	}
	cycle.Radars, err = store.listAnalysisRadars(ctx, analysisID)
	return cycle, err
}

func (store *Store) listAnalysisRadars(ctx context.Context, analysisID uuid.UUID) ([]workflow.AnalysisRadar, error) {
	rows, err := store.pool.Query(ctx, `
SELECT radar_id, scan_id, state, time_offset_seconds, mean_quality_index,
       exclusion_reason
FROM analysis_cycle_radars
WHERE analysis_id = $1
ORDER BY radar_id`, analysisID)
	if err != nil {
		return nil, fmt.Errorf("list analysis radars: %w", err)
	}
	defer rows.Close()

	items := make([]workflow.AnalysisRadar, 0)
	for rows.Next() {
		var item workflow.AnalysisRadar
		if err := rows.Scan(
			&item.RadarID, &item.ScanID, &item.State, &item.TimeOffsetSeconds,
			&item.MeanQualityIndex, &item.ExclusionReason,
		); err != nil {
			return nil, fmt.Errorf("scan analysis radar: %w", err)
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate analysis radars: %w", err)
	}
	return items, nil
}

const radarSelect = `
SELECT r.radar_id, r.display_name, r.lifecycle, r.current_config_version,
       r.created_at, r.updated_at
FROM radars AS r`

func scanRadar(row rowScanner) (workflow.Radar, error) {
	var radar workflow.Radar
	if err := row.Scan(
		&radar.ID, &radar.DisplayName, &radar.Lifecycle,
		&radar.ConfigVersion, &radar.CreatedAt, &radar.UpdatedAt,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.Radar{}, workflow.ErrNotFound
		}
		return workflow.Radar{}, fmt.Errorf("scan radar: %w", err)
	}
	return radar, nil
}

const radarScanSelect = `
SELECT s.scan_id, r.run_id, s.radar_id, s.volume_start_time, s.volume_end_time,
       s.received_at, r.radar_config_version, r.status, r.degraded_reason,
       r.normalized_uri, r.qc_uri, r.grid_uri, r.scan_completeness,
       r.mean_quality_index, r.created_at, r.updated_at
FROM radar_scans AS s
JOIN radar_scan_runs AS r ON r.scan_id = s.scan_id`

func scanRadarScan(row rowScanner) (workflow.RadarScan, error) {
	var scan workflow.RadarScan
	if err := row.Scan(
		&scan.ID, &scan.RunID, &scan.RadarID, &scan.VolumeStartTime,
		&scan.VolumeEndTime, &scan.ReceivedAt, &scan.RadarConfigVersion,
		&scan.Status, &scan.DegradedReason, &scan.NormalizedURI, &scan.QCURI,
		&scan.GridURI, &scan.ScanCompleteness, &scan.MeanQualityIndex,
		&scan.CreatedAt, &scan.UpdatedAt,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.RadarScan{}, workflow.ErrNotFound
		}
		return workflow.RadarScan{}, fmt.Errorf("scan radar workflow: %w", err)
	}
	return scan, nil
}

const analysisSelect = `
SELECT a.analysis_id, a.run_id, a.analysis_time, a.grid_id, a.config_version,
       a.status, a.degraded_reason, a.radar_count, a.valid_coverage_ratio,
       a.mean_quality_index, a.analysis_uri, a.created_at, a.updated_at
FROM analysis_cycles AS a`

func scanAnalysis(row rowScanner) (workflow.AnalysisCycle, error) {
	var cycle workflow.AnalysisCycle
	if err := row.Scan(
		&cycle.ID, &cycle.RunID, &cycle.AnalysisTime, &cycle.GridID,
		&cycle.ConfigVersion, &cycle.Status, &cycle.DegradedReason,
		&cycle.RadarCount, &cycle.ValidCoverageRatio, &cycle.MeanQualityIndex,
		&cycle.AnalysisURI, &cycle.CreatedAt, &cycle.UpdatedAt,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return workflow.AnalysisCycle{}, workflow.ErrNotFound
		}
		return workflow.AnalysisCycle{}, fmt.Errorf("scan analysis cycle: %w", err)
	}
	return cycle, nil
}

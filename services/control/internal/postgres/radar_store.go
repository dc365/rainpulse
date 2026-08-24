package postgres

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

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
	if _, err := store.GetRadar(ctx, radarID); err != nil {
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
       )
FROM radar_scans AS s
JOIN radar_scan_runs AS r ON r.scan_id = s.scan_id
WHERE s.radar_id = $1
ORDER BY s.volume_end_time DESC, r.created_at DESC
LIMIT 1`, radarID)

	var summary workflow.RadarStatusSummary
	summary.RadarID = radarID
	var status workflow.RadarScanStatus
	if err := row.Scan(
		&summary.LatestScanID, &summary.LatestScanTime, &status,
		&summary.ScanCompleteness, &summary.MeanQualityIndex,
		&summary.DataDelaySeconds, &summary.ParticipatingInLatestAnalysis,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			summary.Health = workflow.RadarHealthUnknown
			return summary, nil
		}
		return workflow.RadarStatusSummary{}, fmt.Errorf("get radar status: %w", err)
	}
	summary.ScanStatus = &status
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

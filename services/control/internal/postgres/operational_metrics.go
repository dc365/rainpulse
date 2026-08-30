package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operationalmetrics"
	"github.com/jackc/pgx/v5"
)

func (store *Store) OperationalMetrics(
	ctx context.Context,
) (operationalmetrics.Snapshot, error) {
	snapshot := operationalmetrics.Snapshot{}
	queries := []struct {
		name   string
		query  string
		target *map[string]int64
	}{
		{"jobs", `SELECT status, count(*) FROM jobs GROUP BY status`, &snapshot.Jobs},
		{"radar scans", `SELECT status, count(*) FROM radar_scan_runs GROUP BY status`, &snapshot.RadarScans},
		{"analysis cycles", `SELECT status, count(*) FROM analysis_cycles GROUP BY status`, &snapshot.AnalysisCycles},
		{"forecast runs", `SELECT status, count(*) FROM forecast_runs GROUP BY status`, &snapshot.ForecastRuns},
		{"outbox events", `SELECT status, count(*) FROM outbox_events GROUP BY status`, &snapshot.Outbox},
	}
	for _, item := range queries {
		values, err := store.countStatuses(ctx, item.query)
		if err != nil {
			return operationalmetrics.Snapshot{}, fmt.Errorf("count %s: %w", item.name, err)
		}
		*item.target = values
	}
	if err := store.pool.QueryRow(ctx, `
SELECT
    (SELECT count(*) FROM radar_scans),
    (SELECT count(*) FROM jobs WHERE job_type = 'radar.decode' AND status = 'FAILED')
`).Scan(&snapshot.RadarScanReceivedTotal, &snapshot.RadarDecodeFailedTotal); err != nil {
		return operationalmetrics.Snapshot{}, fmt.Errorf("count radar ingest outcomes: %w", err)
	}
	snapshot.Radars = make(map[string]operationalmetrics.RadarSnapshot)
	rows, err := store.pool.Query(ctx, `
SELECT r.radar_id, r.lifecycle,
       GREATEST(0, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - latest.volume_end_time)))::double precision,
       health.scan_completeness,
       qc.mean_quality_index,
       qc_job.duration_seconds,
       CASE WHEN health.actual_radial_count > 0
            THEN qc.radial_interference_ray_count::double precision / health.actual_radial_count
       END,
       CASE WHEN qc.valid_gate_count + qc.missing_gate_count > 0
            THEN (qc.ground_clutter_gate_count + qc.sea_clutter_gate_count + qc.ap_gate_count)::double precision /
                 (qc.valid_gate_count + qc.missing_gate_count)
       END,
       CASE WHEN grid.grid_cell_count > 0
            THEN grid.beam_blocked_missing_cell_count::double precision / grid.grid_cell_count
       END,
       CASE WHEN grid.grid_cell_count > 0
            THEN grid.missing_cell_count::double precision / grid.grid_cell_count
       END
FROM radars AS r
LEFT JOIN LATERAL (
    SELECT scans.scan_id, scans.volume_end_time
    FROM radar_scans AS scans
    WHERE scans.radar_id = r.radar_id
    ORDER BY scans.volume_end_time DESC, scans.created_at DESC
    LIMIT 1
) AS latest ON TRUE
LEFT JOIN radar_health_metrics AS health ON health.scan_id = latest.scan_id
LEFT JOIN radar_qc_metrics AS qc ON qc.scan_id = latest.scan_id
LEFT JOIN radar_grid_metrics AS grid ON grid.scan_id = latest.scan_id
LEFT JOIN LATERAL (
    SELECT EXTRACT(EPOCH FROM (jobs.completed_at - jobs.started_at))::double precision AS duration_seconds
    FROM jobs
    JOIN radar_scan_runs AS runs ON runs.run_id = jobs.run_id
    WHERE runs.radar_id = r.radar_id
      AND jobs.job_type = 'radar.qc'
      AND jobs.status = 'SUCCEEDED'
      AND jobs.started_at IS NOT NULL
      AND jobs.completed_at IS NOT NULL
    ORDER BY jobs.completed_at DESC
    LIMIT 1
) AS qc_job ON TRUE
ORDER BY r.radar_id`)
	if err != nil {
		return operationalmetrics.Snapshot{}, fmt.Errorf("measure radar operational metrics: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var radarID string
		var lifecycle string
		var delay sql.NullFloat64
		var completeness sql.NullFloat64
		var quality sql.NullFloat64
		var qcDuration sql.NullFloat64
		var interference sql.NullFloat64
		var clutter sql.NullFloat64
		var blockage sql.NullFloat64
		var missing sql.NullFloat64
		if err := rows.Scan(
			&radarID, &lifecycle, &delay, &completeness, &quality, &qcDuration,
			&interference, &clutter, &blockage, &missing,
		); err != nil {
			return operationalmetrics.Snapshot{}, fmt.Errorf("scan radar operational metrics: %w", err)
		}
		snapshot.Radars[radarID] = operationalmetrics.RadarSnapshot{
			Lifecycle: lifecycle, LatestScanAvailable: delay.Valid,
			DataDelaySeconds: nullableFloat(delay),
			ScanCompleteness: nullableFloat(completeness), QCDurationSeconds: nullableFloat(qcDuration),
			MeanQualityIndex: nullableFloat(quality), InterferenceRatio: nullableFloat(interference),
			ClutterRatio: nullableFloat(clutter), BlockageRatio: nullableFloat(blockage),
			MissingRatio: nullableFloat(missing),
		}
	}
	if err := rows.Err(); err != nil {
		return operationalmetrics.Snapshot{}, fmt.Errorf("iterate radar operational metrics: %w", err)
	}
	var radarDelay sql.NullFloat64
	if err := store.pool.QueryRow(ctx, `
SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MAX(volume_end_time)))::double precision
FROM radar_scans`).Scan(&radarDelay); err != nil {
		return operationalmetrics.Snapshot{}, fmt.Errorf("measure radar data delay: %w", err)
	}
	if radarDelay.Valid {
		snapshot.RadarDataDelaySeconds = &radarDelay.Float64
	}
	var pendingAge sql.NullFloat64
	if err := store.pool.QueryRow(ctx, `
SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(created_at)))::double precision
FROM jobs WHERE status IN ('PENDING', 'RUNNING')`).Scan(&pendingAge); err != nil {
		return operationalmetrics.Snapshot{}, fmt.Errorf("measure pending job age: %w", err)
	}
	if pendingAge.Valid {
		snapshot.OldestPendingSeconds = &pendingAge.Float64
	}
	var analysisRadarCount int64
	var analysisCoverage float64
	var analysisPublishDelay float64
	var analysisOperationalEligible bool
	err = store.pool.QueryRow(ctx, `
SELECT analysis.radar_count, analysis.valid_coverage_ratio,
       GREATEST(0, EXTRACT(EPOCH FROM (qpe.measured_at - analysis.analysis_time)))::double precision,
       COALESCE((qpe.diagnostics ->> 'operational_eligible')::boolean, FALSE)
FROM analysis_cycles AS analysis
JOIN LATERAL (
    SELECT measured_at, diagnostics
    FROM qpe_runs
    WHERE analysis_id = analysis.analysis_id
      AND status = 'SUCCEEDED'
      AND measured_at IS NOT NULL
    ORDER BY measured_at DESC
    LIMIT 1
) AS qpe ON TRUE
WHERE analysis.status = 'ANALYSIS_READY'
  AND analysis.valid_coverage_ratio IS NOT NULL
ORDER BY analysis.analysis_time DESC, analysis.created_at DESC
LIMIT 1`).Scan(
		&analysisRadarCount, &analysisCoverage, &analysisPublishDelay,
		&analysisOperationalEligible,
	)
	if err == nil {
		snapshot.AnalysisRadarCount = &analysisRadarCount
		snapshot.AnalysisValidCoverageRatio = &analysisCoverage
		snapshot.AnalysisPublishDelaySeconds = &analysisPublishDelay
		snapshot.AnalysisOperationalEligible = analysisOperationalEligible
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return operationalmetrics.Snapshot{}, fmt.Errorf("measure latest analysis metrics: %w", err)
	}
	return snapshot, nil
}

func nullableFloat(value sql.NullFloat64) *float64 {
	if !value.Valid {
		return nil
	}
	result := value.Float64
	return &result
}

func (store *Store) countStatuses(ctx context.Context, query string) (map[string]int64, error) {
	rows, err := store.pool.Query(ctx, query)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	values := make(map[string]int64)
	for rows.Next() {
		var status string
		var count int64
		if err := rows.Scan(&status, &count); err != nil {
			return nil, err
		}
		values[status] = count
	}
	return values, rows.Err()
}

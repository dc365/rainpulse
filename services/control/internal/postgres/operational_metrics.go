package postgres

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operationalmetrics"
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
	return snapshot, nil
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

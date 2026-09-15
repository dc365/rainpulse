package postgres

import (
	"context"
	"fmt"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workspace"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"time"
)

func (s *Store) HistoricalQCBatch(ctx context.Context, start time.Time, create bool) (*workspace.QCBatch, error) {
	tx, e := s.pool.Begin(ctx)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback(ctx)
	_, e = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended('historical-qc-batch',0))`)
	if e != nil {
		return nil, e
	}
	var id uuid.UUID
	var status string
	e = tx.QueryRow(ctx, `SELECT request_id,status FROM pipeline_regeneration_requests WHERE preset='radar_qc_only' AND issue_time=$1 ORDER BY created_at DESC LIMIT 1`, start).Scan(&id, &status)
	if e != nil && e != pgx.ErrNoRows {
		return nil, e
	}
	if create && (e == pgx.ErrNoRows || status == "SUCCEEDED" || status == "FAILED") {
		var active bool
		if e = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pipeline_regeneration_requests WHERE preset='radar_qc_only' AND status NOT IN ('SUCCEEDED','FAILED'))`).Scan(&active); e != nil {
			return nil, e
		}
		if active {
			return nil, fmt.Errorf("已有质控批次在计算，请等待完成")
		}
		id = uuid.New()
		_, e = tx.Exec(ctx, `INSERT INTO pipeline_regeneration_requests(request_id,issue_time,grid_id,preset,reason,status) VALUES($1,$2,'historical','radar_qc_only','历史案例：仅雷达质控与对照图','PENDING')`, id, start)
		if e != nil {
			return nil, e
		}
		// Match the workspace preferred latest ready analysis at each time and grid.
		_, e = tx.Exec(ctx, `INSERT INTO qc_batch_items(request_id,kind,item_id,item_time)
 SELECT $1::uuid,'display',analysis_id,analysis_time FROM (SELECT DISTINCT ON (analysis_time,grid_id) analysis_id,analysis_time FROM analysis_cycles
 WHERE analysis_time >= $2 AND analysis_time < $3 AND analysis_uri IS NOT NULL AND status='ANALYSIS_READY' ORDER BY analysis_time,grid_id,created_at DESC,analysis_id DESC) selected`, id, start, start.Add(24*time.Hour))
		if e != nil {
			return nil, e
		}
		_, e = tx.Exec(ctx, `INSERT INTO qc_batch_items(request_id,kind,item_id,item_time,radar_id,status,error_message)
 SELECT DISTINCT $1::uuid,'qc',s.scan_id,s.volume_end_time,s.radar_id,
 CASE WHEN r.normalized_uri IS NULL THEN 'FAILED' ELSE 'PENDING' END,
 CASE WHEN r.normalized_uri IS NULL THEN '缺少解码数据，无法进行质控' ELSE '' END
 FROM radar_scans s JOIN radar_scan_runs r USING(scan_id)
 WHERE (s.volume_end_time >= $2 AND s.volume_end_time < $3) OR EXISTS(
 SELECT 1 FROM analysis_cycle_radars a JOIN qc_batch_items i ON i.item_id=a.analysis_id AND i.kind='display'
 WHERE i.request_id=$1 AND a.scan_id=s.scan_id AND a.state='PARTICIPATING')`, id, start, start.Add(24*time.Hour))
		if e != nil {
			return nil, e
		}
		var count int
		if e = tx.QueryRow(ctx, `SELECT count(*) FROM qc_batch_items WHERE request_id=$1 AND kind='qc'`, id).Scan(&count); e != nil {
			return nil, e
		}
		if count == 0 {
			return nil, fmt.Errorf("该日没有可重算雷达数据")
		}
	} else if e == pgx.ErrNoRows {
		return nil, nil
	}
	if e = tx.Commit(ctx); e != nil {
		return nil, e
	}
	return s.ReadQCBatch(ctx, id)
}
func (s *Store) ReadQCBatch(ctx context.Context, id uuid.UUID) (*workspace.QCBatch, error) {
	b := &workspace.QCBatch{ID: id, Items: []workspace.QCBatchItem{}}
	if e := s.pool.QueryRow(ctx, `SELECT status FROM pipeline_regeneration_requests WHERE request_id=$1`, id).Scan(&b.Status); e != nil {
		return nil, e
	}
	rows, e := s.pool.Query(ctx, `SELECT i.kind,i.item_id,i.item_time,i.radar_id,i.job_id,COALESCE(j.status,i.status),COALESCE(a.error_message,i.error_message) FROM qc_batch_items i LEFT JOIN jobs j ON j.job_id=i.job_id LEFT JOIN LATERAL (SELECT error_message FROM job_attempts WHERE job_id=j.job_id ORDER BY attempt_no DESC LIMIT 1) a ON true WHERE i.request_id=$1 ORDER BY i.item_time,i.kind,i.radar_id`, id)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	for rows.Next() {
		var i workspace.QCBatchItem
		if e = rows.Scan(&i.Kind, &i.ID, &i.Time, &i.Radar, &i.JobID, &i.Status, &i.Error); e != nil {
			return nil, e
		}
		b.Items = append(b.Items, i)
	}
	return b, rows.Err()
}
func (s *Store) ActiveQCBatch(ctx context.Context) (*workspace.QCBatch, error) {
	var id uuid.UUID
	e := s.pool.QueryRow(ctx, `SELECT request_id FROM pipeline_regeneration_requests WHERE preset='radar_qc_only' AND status NOT IN ('SUCCEEDED','FAILED') ORDER BY created_at LIMIT 1`).Scan(&id)
	if e == pgx.ErrNoRows {
		return nil, nil
	}
	if e != nil {
		return nil, e
	}
	return s.ReadQCBatch(ctx, id)
}
func (s *Store) RecordQCBatchJob(ctx context.Context, b uuid.UUID, i workspace.QCBatchItem, job uuid.UUID, problem error) error {
	var e error
	if problem != nil {
		_, e = s.pool.Exec(ctx, `UPDATE qc_batch_items SET status='FAILED',error_message=$4 WHERE request_id=$1 AND kind=$2 AND item_id=$3`, b, i.Kind, i.ID, problem.Error())
	} else {
		_, e = s.pool.Exec(ctx, `UPDATE qc_batch_items SET job_id=$4 WHERE request_id=$1 AND kind=$2 AND item_id=$3`, b, i.Kind, i.ID, job)
	}
	return e
}
func (s *Store) SetQCBatchStatus(ctx context.Context, id uuid.UUID, status string) error {
	_, e := s.pool.Exec(ctx, `UPDATE pipeline_regeneration_requests SET status=$2,updated_at=now() WHERE request_id=$1`, id, status)
	return e
}

// Do not auto-grid historical QC-only outputs, even after the batch completes.
func (s *Store) IsQCOnlyScan(ctx context.Context, id uuid.UUID) (bool, error) {
	var found bool
	e := s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM qc_batch_items WHERE kind='qc' AND item_id=$1)`, id).Scan(&found)
	return found, e
}
func (s *Store) QCBatchDisplayReady(ctx context.Context, b, id uuid.UUID) (bool, bool, error) {
	var pending, failed int
	e := s.pool.QueryRow(ctx, `SELECT count(*) FILTER (WHERE COALESCE(j.status,i.status,'FAILED') NOT IN ('SUCCEEDED','FAILED','SKIPPED')),
 count(*) FILTER (WHERE COALESCE(j.status,i.status,'FAILED') IN ('FAILED','SKIPPED'))
 FROM analysis_cycle_radars a LEFT JOIN qc_batch_items i ON i.request_id=$1 AND i.kind='qc' AND i.item_id=a.scan_id
 LEFT JOIN jobs j ON j.job_id=i.job_id WHERE a.analysis_id=$2 AND a.state='PARTICIPATING'`, b, id).Scan(&pending, &failed)
	return pending == 0, failed > 0, e
}

package operations

import (
	"context"
	"database/sql"
	"encoding/json"
	"strings"
	"time"
)

// A row lock serializes GC admission against task admission without holding a
// second connection or keeping a DB transaction open while deleting objects.
func storageAdmission(ctx context.Context, tx *sql.Tx, newWork bool) error {
	var session sql.NullString
	var report string
	var threshold, age int
	var minFree uint64
	if e := tx.QueryRowContext(ctx, `SELECT session_id::text,pressure_report,inode_stop_percent,minimum_free_bytes,report_max_age_seconds FROM ops_storage_control WHERE id=1 FOR SHARE`).Scan(&session, &report, &threshold, &minFree, &age); e != nil {
		return e
	}
	if session.Valid {
		return Conflict("候选存储正在清理；新的管理操作暂停，业务自动链路不受影响")
	}
	if !newWork || report == "" {
		return nil
	}
	var raw []byte
	if e := tx.QueryRowContext(ctx, `SELECT document FROM ops_storage_reports WHERE id=$1`, report).Scan(&raw); e != nil {
		return Conflict("未收到所配置的主机存储采样")
	}
	var r StorageReport
	if e := json.Unmarshal(raw, &r); e != nil {
		return e
	}
	if msg := PressureProblem(r, time.Now().UTC(), time.Duration(age)*time.Second, threshold, minFree); msg != "" {
		return Conflict(msg)
	}
	return nil
}
func liveStorageRun(ctx context.Context, tx *sql.Tx, id string) error {
	var retired bool
	if e := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM ops_retired_runs WHERE run_id=$1)`, id).Scan(&retired); e != nil {
		return e
	}
	if retired {
		return problem(410, "candidate_retired", "候选数据已按保留策略退役；历史记录保留，请建立新的重算计划")
	}
	return nil
}
func liveStorageInputs(ctx context.Context, tx *sql.Tx, specs []Spec) error {
	seen := map[string]bool{}
	for _, s := range specs {
		uris := append([]string(nil), s.InputURIs...)
		for _, a := range s.Inputs {
			uris = append(uris, a.URI)
		}
		for _, u := range uris {
			if run, ok := CandidateRunFromURI(u); ok && !seen[run] {
				if e := liveStorageRun(ctx, tx, run); e != nil {
					return e
				}
				seen[run] = true
			}
		}
	}
	return nil
}
func storageEvent(ctx context.Context, tx *sql.Tx, event, actor string, value any) error {
	_, e := tx.ExecContext(ctx, `INSERT INTO ops_storage_events(event,actor,document) VALUES($1,$2,$3)`, event, actor, string(JSON(value)))
	return e
}
func (s *Store) ReportStorage(ctx context.Context, r StorageReport) error {
	if e := r.Validate(time.Now().UTC()); e != nil {
		return e
	}
	// Admin-authenticated snapshots only; no arbitrary host shell from the web.
	_, e := s.DB.ExecContext(ctx, `INSERT INTO ops_storage_reports(id,document) VALUES($1,$2)
 ON CONFLICT(id) DO UPDATE SET document=excluded.document,received_at=now()
 WHERE (ops_storage_reports.document->>'sampled_at')::timestamptz <= ($2::jsonb->>'sampled_at')::timestamptz`, r.ID, string(JSON(r)))
	return e
}
func (s *Store) StorageStatus(ctx context.Context) (map[string]any, error) {
	out := map[string]any{"scope": "managed_candidates_only", "sampled_at": time.Now().UTC()}
	var raw []byte
	e := s.DB.QueryRowContext(ctx, `SELECT to_jsonb(c) FROM ops_storage_control c WHERE id=1`).Scan(&raw)
	if e != nil {
		return nil, e
	}
	out["control"] = json.RawMessage(raw)
	rows, e := s.DB.QueryContext(ctx, `SELECT jsonb_build_object('report',document,'received_at',received_at) FROM ops_storage_reports ORDER BY received_at DESC LIMIT 64`)
	if e != nil {
		return nil, e
	}
	reports, e := rawRows(rows)
	rows.Close()
	if e != nil {
		return nil, e
	}
	out["reports"] = reports
	rows, e = s.DB.QueryContext(ctx, `SELECT jsonb_build_object('id',p.id,'digest',p.digest,'state',p.state,'created_at',p.created_at,'finished_at',p.finished_at,'receipt',p.receipt,'targets',jsonb_array_length(p.document->'targets')) FROM ops_retention_plans p ORDER BY p.created_at DESC LIMIT 50`)
	if e != nil {
		return nil, e
	}
	plans, e := rawRows(rows)
	rows.Close()
	out["plans"] = plans
	return out, e
}

type PressureAction struct {
	ReportID         string `json:"report_id"`
	InodeStop        int    `json:"inode_stop_percent"`
	MinimumFreeBytes int64  `json:"minimum_free_bytes"`
	MaxAgeSeconds    int    `json:"report_max_age_seconds"`
	ExpectedRevision int64  `json:"expected_revision"`
	Reason           string `json:"reason"`
}

func (s *Store) SetPressure(ctx context.Context, a PressureAction) error {
	if (a.ReportID != "" && !namePattern.MatchString(a.ReportID)) || a.InodeStop < 50 || a.InodeStop > 99 || a.MinimumFreeBytes < 0 || a.MaxAgeSeconds < 30 || a.MaxAgeSeconds > 3600 || strings.TrimSpace(a.Reason) == "" || len(a.Reason) > 512 {
		return Invalid("请提供有效的存储门禁参数及原因")
	}
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	var rev int64
	var session sql.NullString
	if e = tx.QueryRowContext(ctx, `SELECT revision,session_id::text FROM ops_storage_control WHERE id=1 FOR UPDATE`).Scan(&rev, &session); e != nil {
		return e
	}
	if rev != a.ExpectedRevision || session.Valid {
		return Conflict("存储策略已改变或正在清理")
	}
	if a.ReportID != "" {
		var raw []byte
		if e = tx.QueryRowContext(ctx, `SELECT document FROM ops_storage_reports WHERE id=$1`, a.ReportID).Scan(&raw); e != nil {
			return Conflict("请先上传真实主机挂载点采样")
		}
		var r StorageReport
		if e = json.Unmarshal(raw, &r); e != nil {
			return e
		}
		if r.InodeUsedPercent == nil || time.Since(r.SampledAt) > time.Duration(a.MaxAgeSeconds)*time.Second {
			return Conflict("采样已过期或缺少inode计数")
		}
	}
	_, e = tx.ExecContext(ctx, `UPDATE ops_storage_control SET pressure_report=$1,inode_stop_percent=$2,minimum_free_bytes=$3,report_max_age_seconds=$4,revision=revision+1,updated_at=now() WHERE id=1`, a.ReportID, a.InodeStop, a.MinimumFreeBytes, a.MaxAgeSeconds)
	if e != nil {
		return e
	}
	a.Reason = Redact(a.Reason)
	if e = storageEvent(ctx, tx, "pressure.updated", "administrator", a); e != nil {
		return e
	}
	return tx.Commit()
}
func (s *Store) PinStorage(ctx context.Context, run, reason string, pin bool) error {
	if !ValidID(run) || strings.TrimSpace(reason) == "" || len(reason) > 512 {
		return Invalid("请选择作业并填写保留原因")
	}
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	if e = storageAdmission(ctx, tx, false); e != nil {
		return e
	}
	if e = liveStorageRun(ctx, tx, run); e != nil {
		return e
	}
	if pin {
		_, e = tx.ExecContext(ctx, `INSERT INTO ops_retention_pins(run_id,reason,actor) VALUES($1,$2,'administrator') ON CONFLICT(run_id) DO UPDATE SET reason=excluded.reason,updated_at=now()`, run, Redact(reason))
	} else {
		_, e = tx.ExecContext(ctx, `DELETE FROM ops_retention_pins WHERE run_id=$1`, run)
	}
	if e != nil {
		return e
	}
	if e = storageEvent(ctx, tx, "retention.pin", "administrator", map[string]any{"run_id": run, "pin": pin, "reason": Redact(reason)}); e != nil {
		return e
	}
	return tx.Commit()
}

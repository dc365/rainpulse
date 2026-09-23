package operations

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/url"
	"os"
	"strings"
	"time"
)

// A retention unit is an entire managed run (QC + dependent review). A run is
// protected if any of its results wins a slot, avoiding half-deleted pairs.
const retentionCandidatesSQL = `WITH good AS (
 SELECT t.*, coalesce(nullif(t.spec#>>'{request,payload,scan_id}',''),nullif(t.spec#>>'{request,payload,analysis_id}',''),t.id::text) AS slot,
 coalesce((t.result->>'finished_at')::timestamptz,t.updated_at) AS done
 FROM ops_tasks t WHERE t.state='SUCCEEDED' AND NOT EXISTS(SELECT 1 FROM ops_retired_runs z WHERE z.run_id=t.run_id)
), ranked AS (
 SELECT g.*,row_number() OVER(PARTITION BY kind,slot ORDER BY done DESC,id DESC) AS newest,
 row_number() OVER(PARTITION BY kind,slot,spec#>>'{identity,fingerprint}' ORDER BY done DESC,id DESC) AS version_newest
 FROM good g
), winners AS (
 SELECT DISTINCT run_id FROM ranked t WHERE newest<=$1 OR
 (version_newest<=$1 AND EXISTS(SELECT 1 FROM ops_release_channels c WHERE c.kind=t.kind AND
 (c.current_fingerprint=t.spec#>>'{identity,fingerprint}' OR
 (c.previous_fingerprint=t.spec#>>'{identity,fingerprint}' AND c.updated_at>now()-interval '48 hours'))))
)
SELECT r.id::text,r.name,r.updated_at
FROM ops_runs r WHERE r.mode<>'PAUSED' AND r.state IN('SUCCEEDED','FAILED','PARTIAL_SUCCESS','CANCELLED')
 AND r.updated_at < now() - (greatest($2,CASE WHEN r.state='SUCCEEDED' THEN 24 ELSE 168 END) * interval '1 hour')
 AND EXISTS(SELECT 1 FROM ops_attempts a JOIN ops_tasks t ON t.id=a.task_id WHERE t.run_id=r.id)
 AND NOT EXISTS(SELECT 1 FROM ops_tasks t WHERE t.run_id=r.id AND t.state IN('WAITING','QUEUED','RUNNING','COMMITTING'))
 AND NOT EXISTS(SELECT 1 FROM ops_attempts a JOIN ops_tasks t ON t.id=a.task_id WHERE t.run_id=r.id AND a.state IN('RUNNING','COMMITTING'))
 AND NOT EXISTS(SELECT 1 FROM winners w WHERE w.run_id=r.id)
 AND NOT EXISTS(SELECT 1 FROM ops_retention_pins p WHERE p.run_id=r.id)
 AND NOT EXISTS(SELECT 1 FROM ops_retired_runs z WHERE z.run_id=r.id)
 AND ($4::jsonb IS NULL OR r.id::text IN (SELECT jsonb_array_elements_text($4::jsonb)))
 ORDER BY r.updated_at,r.id LIMIT $3`

// References from the formal chain are protected even though candidate-only
// code never publishes there. No dependency table present => fail closed.
const retentionReferencesSQL = `SELECT
 EXISTS(SELECT 1 FROM ops_tasks WHERE run_id<>$1::uuid AND strpos(spec::text,$2)>0) OR
 EXISTS(SELECT 1 FROM ops_plans WHERE submitted_at IS NULL AND expires_at>now() AND strpos(document::text,$2)>0) OR
 EXISTS(SELECT 1 FROM jobs WHERE strpos(request_payload::text,$2)>0) OR
 EXISTS(SELECT 1 FROM radar_scan_runs WHERE starts_with(coalesce(normalized_uri,''),$2) OR starts_with(coalesce(qc_uri,''),$2) OR starts_with(coalesce(grid_uri,''),$2)) OR
 EXISTS(SELECT 1 FROM analysis_cycles WHERE starts_with(coalesce(mosaic_uri,''),$2) OR starts_with(coalesce(analysis_uri,''),$2)) OR
 EXISTS(SELECT 1 FROM input_assets WHERE starts_with(object_uri,$2)) OR
 EXISTS(SELECT 1 FROM product_assets WHERE starts_with(object_uri,$2))`

func retentionTargets(ctx context.Context, tx *sql.Tx, p RetentionPolicy, ids []string) ([]RetentionTarget, error) {
	var filter any
	if ids != nil {
		filter = string(JSON(ids))
	}
	// Scan at most 100 terminal run records per explicit preview, not all history
	// into application memory. SQL ranks all successful slots for correctness.
	rows, e := tx.QueryContext(ctx, retentionCandidatesSQL, p.KeepLatest, p.MinimumAgeHours, p.Limit*5, filter)
	if e != nil {
		return nil, e
	}
	candidates := []RetentionTarget{}
	for rows.Next() {
		var t RetentionTarget
		if e = rows.Scan(&t.RunID, &t.Name, &t.UpdatedAt); e != nil {
			rows.Close()
			return nil, e
		}
		candidates = append(candidates, t)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	targets := []RetentionTarget{}
	for _, t := range candidates {
		var refs bool
		if e = tx.QueryRowContext(ctx, retentionReferencesSQL, t.RunID, managedPrefix(t.RunID)).Scan(&refs); e != nil {
			return nil, e
		}
		if refs {
			continue
		}
		ar, e := tx.QueryContext(ctx, `SELECT a.id::text,t.id::text,t.kind,a.request,coalesce(a.result,'null'::jsonb) FROM ops_attempts a JOIN ops_tasks t ON t.id=a.task_id WHERE t.run_id=$1 ORDER BY t.ordinal,a.number LIMIT 2049`, t.RunID)
		if e != nil {
			return nil, e
		}
		t.Attempts = []RetentionAttempt{}
		for ar.Next() {
			var a RetentionAttempt
			var req, result []byte
			if e = ar.Scan(&a.ID, &a.TaskID, &a.Kind, &req, &result); e != nil {
				ar.Close()
				return nil, e
			}
			var r struct {
				Payload struct {
					OutputPrefix string `json:"output_prefix"`
				} `json:"payload"`
			}
			if e = json.Unmarshal(req, &r); e != nil {
				ar.Close()
				return nil, e
			}
			a.Prefix = r.Payload.OutputPrefix
			var c Candidate
			if string(result) != "null" {
				if e = json.Unmarshal(result, &c); e != nil {
					ar.Close()
					return nil, e
				}
				a.MarkerSHA256 = c.Asset.MarkerSHA256
				t.LogicalBytes += c.Asset.SizeBytes
			}
			if e = ValidateAttemptPrefix(t.RunID, a); e != nil {
				ar.Close()
				return nil, e
			}
			t.Attempts = append(t.Attempts, a)
		}
		e = ar.Err()
		ar.Close()
		if e != nil {
			return nil, e
		}
		if len(t.Attempts) == 0 || len(t.Attempts) > 2048 {
			return nil, Invalid("作业尝试目录数量超限，请拆分维护范围")
		}
		targets = append(targets, t)
		if len(targets) >= p.Limit {
			break
		}
	}
	return targets, nil
}
func (s *Store) PreviewRetention(ctx context.Context, policy RetentionPolicy) (RetentionPlan, error) {
	var p RetentionPlan
	if e := policy.Validate(); e != nil {
		return p, e
	}
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return p, e
	}
	defer tx.Rollback()
	if e = storageAdmission(ctx, tx, false); e != nil {
		return p, e
	}
	targets, e := retentionTargets(ctx, tx, policy, nil)
	if e != nil {
		return p, e
	}
	endpoint := strings.TrimRight(os.Getenv("RAINPULSE_OBJECT_STORE_ENDPOINT"), "/")
	u, ue := url.Parse(endpoint)
	if ue != nil || u.Host == "" || u.User != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
		return p, Invalid("服务端对象存储地址未配置；不能冻结清理目标")
	}
	now := time.Now().UTC()
	p = RetentionPlan{ID: NewID(), Policy: policy, Targets: targets, CreatedAt: now, ExpiresAt: now.Add(15 * time.Minute), Scope: "managed_candidates_only", ObjectStoreEndpoint: endpoint}
	p.Digest = CanonicalDigest(p)
	_, e = tx.ExecContext(ctx, `INSERT INTO ops_retention_plans(id,document,digest,expires_at) VALUES($1,$2,$3,$4)`, p.ID, string(JSON(p)), p.Digest, p.ExpiresAt)
	if e != nil {
		return p, e
	}
	if e = storageEvent(ctx, tx, "retention.preview", "administrator", map[string]any{"plan_id": p.ID, "target_count": len(targets), "policy": policy}); e != nil {
		return p, e
	}
	return p, tx.Commit()
}
func (s *Store) RetentionPlan(ctx context.Context, id string) (RetentionPlan, string, error) {
	var raw []byte
	var state string
	e := s.DB.QueryRowContext(ctx, `SELECT document,state FROM ops_retention_plans WHERE id=$1`, id).Scan(&raw, &state)
	if e != nil {
		return RetentionPlan{}, state, e
	}
	p, e := decodeRetention(raw)
	return p, state, e
}

// Begin is the irrevocable logical retirement boundary. It never deletes data.
// Physical purge runs on the host via the S3 API with its own explicit confirm.
func (s *Store) BeginRetention(ctx context.Context, id, digest string) (RetentionPlan, error) {
	var p RetentionPlan
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return p, e
	}
	defer tx.Rollback()
	var session sql.NullString
	if e = tx.QueryRowContext(ctx, `SELECT session_id::text FROM ops_storage_control WHERE id=1 FOR UPDATE`).Scan(&session); e != nil {
		return p, e
	}
	if session.Valid && session.String != id {
		return p, Conflict("已有另一清理会话，先完成或恢复该会话")
	}
	var raw []byte
	var state string
	if e = tx.QueryRowContext(ctx, `SELECT document,state FROM ops_retention_plans WHERE id=$1 FOR UPDATE`, id).Scan(&raw, &state); e != nil {
		return p, e
	}
	if p, e = decodeRetention(raw); e != nil {
		return p, e
	}
	if e = p.ValidateIntegrity(); e != nil {
		return p, e
	}
	if digest != p.Digest || !shaPattern.MatchString(digest) {
		return p, Invalid("必须确认服务端冻结的清理摘要")
	}
	if state == "COMPLETE" {
		return p, Conflict("清理已完成")
	}
	var working bool
	if e = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM ops_attempts WHERE state IN('RUNNING','COMMITTING')) OR EXISTS(SELECT 1 FROM ops_pool_controls WHERE mode<>'DRAINING') OR EXISTS(SELECT 1 FROM ops_workers WHERE (ready OR busy) AND seen_at>now()-interval '75 seconds')`).Scan(&working); e != nil {
		return p, e
	}
	if working {
		return p, Conflict("请先暂停所有管理池并排空真实尝试；租约超时不视为已退出")
	}
	if state == "PREVIEW" {
		if e = p.Validate(time.Now().UTC()); e != nil {
			return p, e
		}
		if len(p.Targets) == 0 {
			return p, Invalid("没有可清理目标")
		}
		ids := []string{}
		for _, t := range p.Targets {
			ids = append(ids, t.RunID)
		}
		current, e := retentionTargets(ctx, tx, p.Policy, ids)
		if e != nil {
			return p, e
		}
		if CanonicalDigest(current) != CanonicalDigest(p.Targets) {
			return p, Conflict("引用、保留版本或任务状态已变化，请重新预览")
		}
		for _, t := range p.Targets {
			if _, e = tx.ExecContext(ctx, `INSERT INTO ops_retired_runs(run_id,plan_id) VALUES($1,$2)`, t.RunID, id); e != nil {
				return p, e
			}
		}
	}
	// ERROR/DELETING resumes exactly the same targets, including after API restart.
	if _, e = tx.ExecContext(ctx, `UPDATE ops_storage_control SET session_id=$1,revision=revision+1,updated_at=now() WHERE id=1`, id); e != nil {
		return p, e
	}
	if _, e = tx.ExecContext(ctx, `UPDATE ops_retention_plans SET state='DELETING',started_at=coalesce(started_at,now()) WHERE id=$1`, id); e != nil {
		return p, e
	}
	if e = storageEvent(ctx, tx, "retention.begin", "administrator", map[string]any{"plan_id": id, "digest": digest}); e != nil {
		return p, e
	}
	return p, tx.Commit()
}

type PurgePrefixReceipt struct {
	Prefix          string `json:"prefix"`
	DeletedVersions int64  `json:"deleted_versions"`
	Empty           bool   `json:"empty"`
	Error           string `json:"error,omitempty"`
}
type PurgeReceipt struct {
	Digest   string               `json:"digest"`
	Prefixes []PurgePrefixReceipt `json:"prefixes"`
}

func (r PurgeReceipt) Validate(p RetentionPlan) error {
	if r.Digest != p.Digest {
		return Invalid("清理回执摘要不同")
	}
	wanted := map[string]bool{}
	for _, t := range p.Targets {
		for _, a := range t.Attempts {
			wanted[a.Prefix] = true
		}
	}
	for _, v := range r.Prefixes {
		if !wanted[v.Prefix] || v.DeletedVersions < 0 || len(v.Error) > 1024 {
			return Invalid("清理回执目录重复、未知或数量非法")
		}
		delete(wanted, v.Prefix)
	}
	if len(wanted) > 0 {
		return Invalid("清理回执缺少目标目录（失败也必须报告）")
	}
	return nil
}
func (s *Store) FinishRetention(ctx context.Context, id string, r PurgeReceipt) error {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	var session sql.NullString
	if e = tx.QueryRowContext(ctx, `SELECT session_id::text FROM ops_storage_control WHERE id=1 FOR UPDATE`).Scan(&session); e != nil {
		return e
	}
	var raw []byte
	var state string
	if e = tx.QueryRowContext(ctx, `SELECT document,state FROM ops_retention_plans WHERE id=$1 FOR UPDATE`, id).Scan(&raw, &state); e != nil {
		return e
	}
	p, e := decodeRetention(raw)
	if e != nil {
		return e
	}
	if e = r.Validate(p); e != nil {
		return e
	}
	if state == "COMPLETE" {
		return nil
	}
	if !session.Valid || session.String != id || state != "DELETING" {
		return Conflict("该清理会话没有持有维护权")
	}
	success := true
	for i, v := range r.Prefixes {
		if !v.Empty || v.Error != "" {
			success = false
		}
		r.Prefixes[i].Error = Redact(v.Error)
	}
	target := "ERROR"
	if success {
		target = "COMPLETE"
		if _, e = tx.ExecContext(ctx, `UPDATE ops_retired_runs SET state='DELETED',deleted_at=now() WHERE plan_id=$1`, id); e != nil {
			return e
		}
	}
	if _, e = tx.ExecContext(ctx, `UPDATE ops_retention_plans SET state=$2,finished_at=now(),receipt=$3 WHERE id=$1`, id, target, string(JSON(r))); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, `UPDATE ops_storage_control SET session_id=NULL,revision=revision+1,updated_at=now() WHERE id=1`); e != nil {
		return e
	}
	if e = storageEvent(ctx, tx, "retention."+strings.ToLower(target), "administrator", map[string]any{"plan_id": id, "receipt": r}); e != nil {
		return e
	}
	return tx.Commit()
}

// Successful byte deletion is reported separately from logical retirement.
// Neither the task result, original plan, logs nor config provenance is erased.
func (s *Store) RunStorageState(ctx context.Context, run string) (string, error) {
	var state string
	e := s.DB.QueryRowContext(ctx, `SELECT state FROM ops_retired_runs WHERE run_id=$1`, run).Scan(&state)
	if e == sql.ErrNoRows {
		return "AVAILABLE", nil
	}
	return state, e
}

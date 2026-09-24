package operations

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

type Store struct{ DB *sql.DB }

func (s *Store) Ready(ctx context.Context) error {
	var v int
	if err := s.DB.QueryRowContext(ctx, "SELECT version FROM ops_schema WHERE version=3").Scan(&v); err != nil {
		return wrapError(err)
	}
	return nil
}
func (s *Store) SavePlan(ctx context.Context, p Plan) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = storageAdmission(ctx, tx, true); err != nil {
		return err
	}
	if err = liveStorageInputs(ctx, tx, p.Tasks); err != nil {
		return err
	}
	// Blocked plans are still kept for diagnosis. A submittable plan freezes the
	// default selection under a shared channel lock.
	if p.Submittable {
		if err = validateReleaseSelection(ctx, tx, p.Tasks); err != nil {
			return err
		}
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO ops_plans(id,run_id,document,digest,expires_at,created_at) VALUES($1,$2,$3,$4,$5,$6)`, p.ID, p.RunID, string(JSON(p)), p.Digest, p.ExpiresAt, p.CreatedAt)
	if err != nil {
		return wrapError(err)
	}
	return tx.Commit()
}
func (s *Store) Plan(ctx context.Context, id string) (Plan, error) {
	var p Plan
	var raw []byte
	err := s.DB.QueryRowContext(ctx, `SELECT document FROM ops_plans WHERE id=$1`, id).Scan(&raw)
	if errors.Is(err, sql.ErrNoRows) {
		return p, ErrNotFound
	}
	if err != nil {
		return p, wrapError(err)
	}
	err = json.Unmarshal(raw, &p)
	return p, err
}
func (s *Store) SubmittedRun(ctx context.Context, id string) (string, error) {
	var run string
	err := s.DB.QueryRowContext(ctx, `SELECT run_id::text FROM ops_plans WHERE id=$1 AND submitted_at IS NOT NULL`, id).Scan(&run)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return run, wrapError(err)
}

// Submit locks the immutable plan, then creates run/tasks/outbox in one transaction.
// A double click, response loss or competing API instance returns the same run.
func (s *Store) Submit(ctx context.Context, p Plan, key, actor string) (string, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", wrapError(err)
	}
	defer tx.Rollback()
	if err = storageAdmission(ctx, tx, true); err != nil {
		return "", err
	}
	if err = liveStorageInputs(ctx, tx, p.Tasks); err != nil {
		return "", err
	}
	if _, err = tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, "ops-submit:"+key); err != nil {
		return "", err
	}
	var prior string
	err = tx.QueryRowContext(ctx, `SELECT id::text FROM ops_plans WHERE idempotency_key=$1`, key).Scan(&prior)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return "", err
	}
	if prior != "" && prior != p.ID {
		return "", Conflict("幂等键已用于另一计划")
	}
	var raw []byte
	var submitted sql.NullTime
	var expires time.Time
	if err = tx.QueryRowContext(ctx, `SELECT document,submitted_at,expires_at FROM ops_plans WHERE id=$1 FOR UPDATE`, p.ID).Scan(&raw, &submitted, &expires); err != nil {
		return "", err
	}
	var stored Plan
	if err = json.Unmarshal(raw, &stored); err != nil {
		return "", err
	}
	if submitted.Valid {
		return stored.RunID, tx.Commit()
	}
	if time.Now().After(expires) || !stored.Submittable || stored.Digest != p.Digest {
		return "", Conflict("计划已过期或检查条件不一致，请重新预检查")
	}
	if err = liveStorageInputs(ctx, tx, stored.Tasks); err != nil {
		return "", err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO ops_runs(id,plan_id,name,actor,impact) VALUES($1,$2,$3,$4,$5)`, p.RunID, p.ID, p.Selection.Name, actor, p.Impact)
	if err != nil {
		return "", err
	}
	for i, spec := range stored.Tasks {
		state := "QUEUED"
		if spec.ParentID != "" {
			state = "WAITING"
		}
		_, err = tx.ExecContext(ctx, `INSERT INTO ops_tasks(id,run_id,parent_id,ordinal,kind,spec,state,queued_at) VALUES($1,$2,NULLIF($3,'')::uuid,$4,$5,$6,$7,CASE WHEN $7='QUEUED' THEN now() ELSE NULL END)`, spec.ID, p.RunID, spec.ParentID, i, spec.Kind, string(JSON(spec)), state)
		if err != nil {
			return "", err
		}
		if state == "QUEUED" {
			if err = enqueue(ctx, tx, spec.ID, spec.Kind, 1); err != nil {
				return "", err
			}
		}
	}
	if _, err = tx.ExecContext(ctx, `UPDATE ops_plans SET submitted_at=now(),idempotency_key=$2 WHERE id=$1`, p.ID, key); err != nil {
		return "", err
	}
	if err = addEvent(ctx, tx, p.RunID, "", "", "info", "run.submitted", "预检查计划已受理；结果仅作为候选保存"); err != nil {
		return "", err
	}
	return p.RunID, tx.Commit()
}
func enqueue(ctx context.Context, tx *sql.Tx, id, kind string, generation int) error {
	eventID := NewID()
	payload := JSON(map[string]any{"schema_version": Version, "event_type": "ops.task.requested.v1", "task_id": id, "generation": generation})
	_, err := tx.ExecContext(ctx, `INSERT INTO outbox_events(event_id,aggregate_type,aggregate_id,event_type,event_version,subject,payload,status,available_at,created_at) VALUES($1,'ops_task',$2,'ops.task.requested.v1',1,$3,$4,'pending',now(),now())`, eventID, id, "rainpulse.jobs.requested.ops."+kind, string(payload))
	return err
}
func addEvent(ctx context.Context, tx *sql.Tx, run, task, attempt, level, event, message string) error {
	_, err := tx.ExecContext(ctx, `INSERT INTO ops_events(run_id,task_id,attempt_id,level,event,message) VALUES($1,NULLIF($2,'')::uuid,NULLIF($3,'')::uuid,$4,$5,$6)`, run, task, attempt, level, event, Redact(message))
	return err
}

const taskSelect = `SELECT id::text,run_id::text,spec,state,generation,attempt_no,COALESCE(current_attempt::text,''),error_code,error_message,created_at,dispatched_at,queued_at,updated_at,COALESCE(result,'null'::jsonb) FROM ops_tasks `

type scanner interface{ Scan(...any) error }

func scanTask(row scanner) (Task, error) {
	var t Task
	var raw []byte
	err := row.Scan(&t.ID, &t.RunID, &raw, &t.State, &t.Generation, &t.AttemptNo, &t.CurrentAttempt, &t.ErrorCode, &t.ErrorMessage, &t.CreatedAt, &t.DispatchedAt, &t.QueuedAt, &t.UpdatedAt, &t.Result)
	if errors.Is(err, sql.ErrNoRows) {
		return t, ErrNotFound
	}
	if err == nil {
		err = json.Unmarshal(raw, &t.Spec)
	}
	return t, err
}
func (s *Store) Task(ctx context.Context, id string) (Task, error) {
	t, err := scanTask(s.DB.QueryRowContext(ctx, taskSelect+`WHERE id=$1`, id))
	if err != nil {
		return t, err
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT id::text,task_id::text,number,worker_id,state,stage,started_at,queued_at,heartbeat_at,lease_until,finished_at,request,COALESCE(result,'null'::jsonb),metrics FROM ops_attempts WHERE task_id=$1 ORDER BY number`, id)
	if err != nil {
		return t, err
	}
	defer rows.Close()
	t.Attempts = []Attempt{}
	for rows.Next() {
		var a Attempt
		var metrics []byte
		if err = rows.Scan(&a.ID, &a.TaskID, &a.Number, &a.WorkerID, &a.State, &a.Stage, &a.StartedAt, &a.QueuedAt, &a.HeartbeatAt, &a.LeaseUntil, &a.FinishedAt, &a.Request, &a.Result, &metrics); err != nil {
			return t, err
		}
		if err = json.Unmarshal(metrics, &a.Metrics); err != nil {
			return t, err
		}
		t.Attempts = append(t.Attempts, a)
	}
	if err = rows.Err(); err != nil {
		return t, err
	}
	rows.Close()
	t.Actions = []string{}
	if t.State == "RUNNING" || t.State == "COMMITTING" {
		t.Actions = append(t.Actions, "recover")
		for _, a := range t.Attempts {
			if a.ID == t.CurrentAttempt && time.Now().After(a.LeaseUntil) {
				t.Stalled = true
				var fresh bool
				if err = s.DB.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM ops_workers WHERE id=$1 AND seen_at>now()-interval '75 seconds')`, a.WorkerID).Scan(&fresh); err != nil {
					return t, err
				}
				if !fresh {
					t.Actions = append(t.Actions, "abandon")
				}
			}
		}
	}
	t.StorageState, err = s.RunStorageState(ctx, t.RunID)
	if err != nil {
		return t, err
	}
	if t.StorageState != "AVAILABLE" {
		t.Actions = []string{}
	}
	return t, nil
}
func (s *Store) Run(ctx context.Context, id string) (Run, error) {
	var r Run
	err := s.DB.QueryRowContext(ctx, `SELECT id::text,plan_id::text,name,mode,state,actor,impact,created_at,updated_at FROM ops_runs WHERE id=$1`, id).Scan(&r.ID, &r.PlanID, &r.Name, &r.Mode, &r.State, &r.Actor, &r.Impact, &r.CreatedAt, &r.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return r, ErrNotFound
	}
	if err != nil {
		return r, err
	}
	rows, err := s.DB.QueryContext(ctx, taskSelect+`WHERE run_id=$1 ORDER BY ordinal`, id)
	if err != nil {
		return r, err
	}
	defer rows.Close()
	r.Tasks = []Task{}
	r.Counts = map[string]int{}
	for rows.Next() {
		t, e := scanTask(rows)
		if e != nil {
			return r, e
		}
		r.Tasks = append(r.Tasks, t)
		r.Counts[t.State]++
	}
	if err = rows.Err(); err != nil {
		return r, err
	}
	rows.Close()
	if err = s.DB.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM ops_tasks t JOIN ops_attempts a ON a.id=t.current_attempt WHERE t.run_id=$1 AND t.state IN('RUNNING','COMMITTING') AND a.lease_until<now())`, id).Scan(&r.Stalled); err != nil {
		return r, err
	}
	r.State = Aggregate(r.Mode, r.Counts)
	r.Actions = AllowedActions(r.Mode, r.State)
	r.StorageState, err = s.RunStorageState(ctx, id)
	if err != nil {
		return r, err
	}
	if r.StorageState != "AVAILABLE" {
		r.Actions = []string{}
	}
	return r, nil
}
func (s *Store) Runs(ctx context.Context, state, q string, before time.Time, beforeID string, limit int) ([]Run, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT r.id::text,r.plan_id::text,r.name,r.mode,r.state,r.actor,r.impact,r.created_at,r.updated_at,
 COALESCE((SELECT jsonb_object_agg(c.state,c.n) FROM (SELECT t.state,count(*) n FROM ops_tasks t WHERE t.run_id=r.id GROUP BY t.state) c),'{}'::jsonb),
 EXISTS(SELECT 1 FROM ops_tasks t JOIN ops_attempts a ON a.id=t.current_attempt WHERE t.run_id=r.id AND t.state IN('RUNNING','COMMITTING') AND a.lease_until<now())
 FROM ops_runs r WHERE ($1='' OR r.state=$1 OR ($1='ATTENTION' AND (r.state IN('FAILED','PARTIAL_SUCCESS') OR EXISTS(SELECT 1 FROM ops_tasks t JOIN ops_attempts a ON a.id=t.current_attempt WHERE t.run_id=r.id AND t.state IN('RUNNING','COMMITTING') AND a.lease_until<now()))))
 AND ($2='' OR position(lower($2) in lower(r.name||' '||r.id::text))>0)
 AND ($3::timestamptz IS NULL OR (r.created_at,r.id)<($3,$4::uuid)) ORDER BY r.created_at DESC,r.id DESC LIMIT $5`, state, q, nullableTime(before), nullableUUID(beforeID), limit+1)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Run{}
	for rows.Next() {
		var r Run
		var counts []byte
		err = rows.Scan(&r.ID, &r.PlanID, &r.Name, &r.Mode, &r.State, &r.Actor, &r.Impact, &r.CreatedAt, &r.UpdatedAt, &counts, &r.Stalled)
		if err != nil {
			return nil, err
		}
		if err = json.Unmarshal(counts, &r.Counts); err != nil {
			return nil, err
		}
		r.Actions = AllowedActions(r.Mode, r.State)
		out = append(out, r)
	}
	return out, rows.Err()
}
func nullableTime(t time.Time) any {
	if t.IsZero() {
		return nil
	}
	return t
}
func nullableUUID(v string) any {
	if v == "" {
		return nil
	}
	return v
}
func (s *Store) Events(ctx context.Context, run, task string, after int64, limit int) ([]Event, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT id,run_id::text,COALESCE(task_id::text,''),COALESCE(attempt_id::text,''),COALESCE(sequence,0),level,event,message,at FROM ops_events WHERE ($1='' OR run_id::text=$1) AND ($2='' OR task_id::text=$2) AND id>$3 ORDER BY id LIMIT $4`, run, task, after, limit+1)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Event{}
	for rows.Next() {
		var e Event
		if err = rows.Scan(&e.ID, &e.RunID, &e.TaskID, &e.AttemptID, &e.Sequence, &e.Level, &e.Event, &e.Message, &e.At); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}
func (s *Store) Register(ctx context.Context, w WorkerInfo) error {
	if !namePattern.MatchString(w.ID) {
		return Invalid("Worker ID非法")
	}
	if err := w.Identity.Validate(); err != nil {
		return err
	}
	if w.CurrentTask != "" && !ValidID(w.CurrentTask) {
		return Invalid("当前任务ID非法")
	}
	_, err := s.DB.ExecContext(ctx, `INSERT INTO ops_workers(id,identity,kind,fingerprint,seen_at,ready,busy,current_task) VALUES($1,$2,$3,$4,now(),$5,$6,$7) ON CONFLICT(id) DO UPDATE SET identity=EXCLUDED.identity,kind=EXCLUDED.kind,fingerprint=EXCLUDED.fingerprint,seen_at=now(),ready=EXCLUDED.ready,busy=EXCLUDED.busy,current_task=EXCLUDED.current_task`, w.ID, string(JSON(w.Identity)), w.Identity.Kind, w.Identity.Fingerprint, w.Ready, w.Busy, w.CurrentTask)
	return wrapError(err)
}
func (s *Store) Workers(ctx context.Context) ([]WorkerInfo, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT w.id,w.identity,w.seen_at,w.ready,w.busy,w.current_task,p.mode FROM ops_workers w JOIN ops_pool_controls p ON p.kind=w.kind ORDER BY w.seen_at DESC,w.id LIMIT 100`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []WorkerInfo{}
	for rows.Next() {
		var w WorkerInfo
		var b []byte
		if err = rows.Scan(&w.ID, &b, &w.SeenAt, &w.Ready, &w.Busy, &w.CurrentTask, &w.PoolMode); err != nil {
			return nil, err
		}
		if err = json.Unmarshal(b, &w.Identity); err != nil {
			return nil, err
		}
		out = append(out, w)
	}
	return out, rows.Err()
}

// Lock order is run -> task -> attempt across claim, finish and operator actions.
func lockTask(ctx context.Context, tx *sql.Tx, id string) (Task, string, error) {
	var run, mode string
	if err := tx.QueryRowContext(ctx, `SELECT run_id::text FROM ops_tasks WHERE id=$1`, id).Scan(&run); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			err = ErrNotFound
		}
		return Task{}, "", err
	}
	if err := tx.QueryRowContext(ctx, `SELECT mode FROM ops_runs WHERE id=$1 FOR UPDATE`, run).Scan(&mode); err != nil {
		return Task{}, "", err
	}
	t, err := scanTask(tx.QueryRowContext(ctx, taskSelect+`WHERE id=$1 FOR UPDATE`, id))
	return t, mode, err
}
func refreshRun(ctx context.Context, tx *sql.Tx, id string) error {
	var mode string
	if err := tx.QueryRowContext(ctx, `SELECT mode FROM ops_runs WHERE id=$1`, id).Scan(&mode); err != nil {
		return err
	}
	rows, err := tx.QueryContext(ctx, `SELECT state,count(*) FROM ops_tasks WHERE run_id=$1 GROUP BY state`, id)
	if err != nil {
		return err
	}
	counts := map[string]int{}
	for rows.Next() {
		var state string
		var n int
		if err = rows.Scan(&state, &n); err != nil {
			rows.Close()
			return err
		}
		counts[state] = n
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `UPDATE ops_runs SET state=$2,updated_at=now() WHERE id=$1`, id, Aggregate(mode, counts))
	return err
}
func (s *Store) Claim(ctx context.Context, id, worker string, generation int) (Claim, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return Claim{}, err
	}
	defer tx.Rollback()
	if err = storageAdmission(ctx, tx, true); err != nil {
		return Claim{}, err
	}
	t, mode, err := lockTask(ctx, tx, id)
	if err != nil {
		return Claim{}, err
	}
	if generation != t.Generation || IsTerminal(t.State) || mode == "CANCELLED" {
		return Claim{Decision: "terminal"}, nil
	}
	if mode == "PAUSED" {
		return Claim{Decision: "paused"}, nil
	}
	if t.State != "QUEUED" {
		return Claim{Decision: "busy"}, nil
	}
	// Pool pause and Claim are serialized by this shared row lock. Existing
	// heartbeat/finish calls intentionally do not acquire the pool lock.
	var poolMode string
	if err = tx.QueryRowContext(ctx, `SELECT mode FROM ops_pool_controls WHERE kind=$1 FOR SHARE`, t.Spec.Kind).Scan(&poolMode); err != nil {
		return Claim{}, err
	}
	if poolMode != "ACCEPTING" {
		return Claim{Decision: "paused"}, nil
	}
	var raw []byte
	var seen time.Time
	var ready bool
	if err = tx.QueryRowContext(ctx, `SELECT identity,seen_at,ready FROM ops_workers WHERE id=$1 FOR UPDATE`, worker).Scan(&raw, &seen, &ready); err != nil {
		return Claim{}, Conflict("Worker尚未登记")
	}
	var identity Identity
	if err = json.Unmarshal(raw, &identity); err != nil {
		return Claim{}, err
	}
	if !ready || time.Since(seen) > WorkerFreshness || identity.Fingerprint != t.Spec.Identity.Fingerprint || identity.Kind != t.Spec.Kind {
		return Claim{}, Conflict("Worker能力或版本与冻结计划不同")
	}
	// The worker row lock prevents concurrent claims for the same registered
	// process. Do not trust a heartbeat's busy=false to grant a second slot.
	var occupied bool
	if err = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM ops_attempts WHERE worker_id=$1 AND state IN ('RUNNING','COMMITTING'))`, worker).Scan(&occupied); err != nil {
		return Claim{}, err
	}
	if occupied {
		return Claim{Decision: "busy"}, nil
	}
	attempt := NewID()
	var secret [32]byte
	if _, err = rand.Read(secret[:]); err != nil {
		return Claim{}, err
	}
	token := hex.EncodeToString(secret[:])
	req, inputs, err := attemptRequest(ctx, tx, t, attempt)
	if err != nil {
		return Claim{}, err
	}
	now := time.Now().UTC()
	_, err = tx.ExecContext(ctx, `INSERT INTO ops_attempts(id,task_id,number,worker_id,token_sha256,state,stage,request,started_at,heartbeat_at,lease_until,queued_at) VALUES($1,$2,$3,$4,$5,'RUNNING','VERIFY_INPUT',$6,$7,$7,$8,$9)`, attempt, id, t.AttemptNo+1, worker, Digest([]byte(token)), string(req), now, now.Add(LeaseDuration), t.QueuedAt)
	if err != nil {
		return Claim{}, err
	}
	_, err = tx.ExecContext(ctx, `UPDATE ops_tasks SET state='RUNNING',attempt_no=attempt_no+1,current_attempt=$2,error_code='',error_message='',updated_at=now() WHERE id=$1`, id, attempt)
	if err != nil {
		return Claim{}, err
	}
	if err = addEvent(ctx, tx, t.RunID, id, attempt, "info", "attempt.started", "Worker已领取并开始核验输入；此时间不是消息派发时间"); err != nil {
		return Claim{}, err
	}
	if err = refreshRun(ctx, tx, t.RunID); err != nil {
		return Claim{}, err
	}
	err = tx.Commit()
	return Claim{Decision: "claimed", TaskID: id, AttemptID: attempt, Token: token, Request: req, Kind: t.Spec.Kind, Identity: &t.Spec.Identity, Inputs: inputs}, err
}
func ArtifactName(kind string) string {
	switch kind {
	case "qc":
		return "qc.zarr"
	case "render":
		return "review-images"
	case "diagnostics":
		return "diagnostics"
	case "multiband":
		return "multiband"
	}
	return ""
}
func attemptRequest(ctx context.Context, tx *sql.Tx, t Task, attempt string) (json.RawMessage, []AssetRef, error) {
	var req map[string]any
	if err := json.Unmarshal(t.Spec.Request, &req); err != nil {
		return nil, nil, err
	}
	payload, ok := req["payload"].(map[string]any)
	if !ok {
		return nil, nil, Invalid("计划缺少执行参数")
	}
	req["job_id"] = t.ID
	req["run_id"] = t.RunID
	req["trace_id"] = t.ID
	req["event_id"] = attempt
	// occurred_at stays frozen: context cutoff must not drift on retries.
	payload["output_prefix"] = "s3://rainpulse/operations/" + t.RunID + "/" + t.ID + "/attempts/" + attempt + "/"
	inputs := append([]AssetRef(nil), t.Spec.Inputs...)
	if t.Spec.ParentID != "" {
		var result []byte
		var state string
		if err := tx.QueryRowContext(ctx, `SELECT state,result FROM ops_tasks WHERE id=$1 AND run_id=$2`, t.Spec.ParentID, t.RunID).Scan(&state, &result); err != nil {
			return nil, nil, err
		}
		if state != "SUCCEEDED" {
			return nil, nil, Conflict("父任务尚未完成")
		}
		var r Candidate
		if err := json.Unmarshal(result, &r); err != nil {
			return nil, nil, err
		}
		payload["input_uri"] = r.Asset.URI
		payload["input_sha256"] = r.Asset.SHA256
		inputs = append(inputs, r.Asset)
	}
	return JSON(req), inputs, nil
}
func authenticateAttempt(ctx context.Context, tx *sql.Tx, t Task, p Pulse) (string, error) {
	if t.CurrentAttempt != p.AttemptID {
		return "", Conflict("当前尝试已改变，旧执行权失效")
	}
	var hash, state string
	if err := tx.QueryRowContext(ctx, `SELECT token_sha256,state FROM ops_attempts WHERE id=$1 AND task_id=$2 FOR UPDATE`, p.AttemptID, t.ID).Scan(&hash, &state); err != nil {
		return "", err
	}
	actual := Digest([]byte(p.Token))
	if subtle.ConstantTimeCompare([]byte(hash), []byte(actual)) != 1 {
		return "", problem(403, "invalid_lease", "执行令牌无效")
	}
	return state, nil
}
func (s *Store) Heartbeat(ctx context.Context, p Pulse) (bool, error) {
	if err := p.Validate(); err != nil {
		return false, err
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	t, mode, err := lockTask(ctx, tx, p.TaskID)
	if err != nil {
		return false, err
	}
	state, err := authenticateAttempt(ctx, tx, t, p)
	if err != nil {
		return false, err
	}
	if IsTerminal(state) || state == "SUPERSEDED" {
		return true, nil
	}
	var previous string
	if err = tx.QueryRowContext(ctx, `SELECT stage FROM ops_attempts WHERE id=$1`, p.AttemptID).Scan(&previous); err != nil {
		return false, err
	}
	_, err = tx.ExecContext(ctx, `UPDATE ops_attempts SET stage=$2,heartbeat_at=now(),lease_until=now()+interval '120 seconds',metrics=metrics||$3::jsonb WHERE id=$1`, p.AttemptID, p.Stage, string(metricJSON(p.Metrics)))
	if err != nil {
		return false, err
	}
	if p.Stage == "UPLOAD" || p.Stage == "COMMIT" {
		if _, err = tx.ExecContext(ctx, `UPDATE ops_tasks SET state='COMMITTING',updated_at=now() WHERE id=$1`, t.ID); err != nil {
			return false, err
		}
	}
	if previous != p.Stage {
		if err = addEvent(ctx, tx, t.RunID, t.ID, p.AttemptID, "info", "attempt.stage", p.Stage); err != nil {
			return false, err
		}
	}
	if err = appendLines(ctx, tx, t, p); err != nil {
		return false, err
	}
	if err = refreshRun(ctx, tx, t.RunID); err != nil {
		return false, err
	}
	return mode == "CANCELLED", tx.Commit()
}
func appendLines(ctx context.Context, tx *sql.Tx, t Task, p Pulse) error {
	var count int
	if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM ops_events WHERE attempt_id=$1 AND sequence IS NOT NULL`, p.AttemptID).Scan(&count); err != nil {
		return err
	}
	for _, l := range p.Lines {
		if count >= 2000 {
			_, err := tx.ExecContext(ctx, `INSERT INTO ops_events(run_id,task_id,attempt_id,sequence,level,event,message) VALUES($1,$2,$3,9223372036854775807,'warning','logs.truncated','日志达到单次尝试2000条上限；完整日志请查容器/Loki') ON CONFLICT DO NOTHING`, t.RunID, t.ID, p.AttemptID)
			return err
		}
		result, err := tx.ExecContext(ctx, `INSERT INTO ops_events(run_id,task_id,attempt_id,sequence,level,event,message) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING`, t.RunID, t.ID, p.AttemptID, l.Sequence, l.Level, l.Event, Redact(l.Message))
		if err != nil {
			return err
		}
		n, _ := result.RowsAffected()
		count += int(n)
	}
	return nil
}

// Finish validates the lease again AFTER storage verification. No stale result
// or cancellation can advance children across this transaction boundary.
func (s *Store) Finish(ctx context.Context, p Finish, result *Candidate, recovered bool) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	t, mode, err := lockTask(ctx, tx, p.TaskID)
	if err != nil {
		return err
	}
	if recovered {
		if t.CurrentAttempt != p.AttemptID {
			return Conflict("恢复期间执行尝试已改变")
		}
	} else {
		if _, err = authenticateAttempt(ctx, tx, t, p.Pulse); err != nil {
			return err
		}
	}
	if IsTerminal(t.State) {
		if t.State == "SUCCEEDED" && p.Outcome == "SUCCEEDED" {
			return nil
		}
		if t.State == "CANCELLED" {
			return nil
		}
		return Conflict("任务已终结")
	}
	outcome := p.Outcome
	if mode == "CANCELLED" {
		outcome = "CANCELLED"
	}
	var resultJSON any
	if result != nil {
		resultJSON = string(JSON(result))
	}
	_, err = tx.ExecContext(ctx, `UPDATE ops_attempts SET state=$2,finished_at=now(),result=$3,metrics=metrics||$4::jsonb WHERE id=$1`, p.AttemptID, outcome, resultJSON, string(metricJSON(p.Metrics)))
	if err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `UPDATE ops_tasks SET state=$2,result=$3,error_code=$4,error_message=$5,updated_at=now() WHERE id=$1`, t.ID, outcome, resultJSON, p.ErrorCode, Redact(p.ErrorMessage))
	if err != nil {
		return err
	}
	if err = appendLines(ctx, tx, t, p.Pulse); err != nil {
		return err
	}
	event := "attempt." + strings.ToLower(outcome)
	message := p.ErrorMessage
	if outcome == "SUCCEEDED" {
		message = "不可变候选结果已登记；未切换业务展示"
	}
	if recovered {
		event = "attempt.registration_recovered"
		message = "从已提交完成标记恢复登记，未重跑算法"
	}
	level := "info"
	if outcome == "FAILED" || outcome == "BLOCKED" {
		level = "error"
	}
	if err = addEvent(ctx, tx, t.RunID, t.ID, p.AttemptID, level, event, message); err != nil {
		return err
	}
	if outcome == "SUCCEEDED" {
		rows, e := tx.QueryContext(ctx, `SELECT id::text,kind,generation FROM ops_tasks WHERE parent_id=$1 AND state='WAITING' ORDER BY ordinal FOR UPDATE`, t.ID)
		if e != nil {
			return e
		}
		type child struct {
			id, kind string
			g        int
		}
		items := []child{}
		for rows.Next() {
			var c child
			if e = rows.Scan(&c.id, &c.kind, &c.g); e != nil {
				rows.Close()
				return e
			}
			items = append(items, c)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		for _, c := range items {
			if _, err = tx.ExecContext(ctx, `UPDATE ops_tasks SET state='QUEUED',queued_at=now(),updated_at=now() WHERE id=$1`, c.id); err != nil {
				return err
			}
			if err = enqueue(ctx, tx, c.id, c.kind, c.g); err != nil {
				return err
			}
		}
	} else {
		_, err = tx.ExecContext(ctx, `UPDATE ops_tasks SET state=CASE WHEN $2='CANCELLED' THEN 'CANCELLED' ELSE 'BLOCKED' END,error_code='UPSTREAM_FAILED',error_message='上游任务未成功；请先恢复上游',updated_at=now() WHERE parent_id=$1 AND state='WAITING'`, t.ID, outcome)
		if err != nil {
			return err
		}
	}
	if err = refreshRun(ctx, tx, t.RunID); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) Action(ctx context.Context, id, action, actor string) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = storageAdmission(ctx, tx, action == "retry_failed" || action == "resume"); err != nil {
		return err
	}
	if err = liveStorageRun(ctx, tx, id); err != nil {
		return err
	}
	var mode, state string
	if err = tx.QueryRowContext(ctx, `SELECT mode,state FROM ops_runs WHERE id=$1 FOR UPDATE`, id).Scan(&mode, &state); errors.Is(err, sql.ErrNoRows) {
		return ErrNotFound
	} else if err != nil {
		return err
	}
	allowed := false
	for _, v := range AllowedActions(mode, state) {
		if v == action {
			allowed = true
		}
	}
	if !allowed {
		return Conflict("当前状态不允许此操作")
	}
	switch action {
	case "pause":
		_, err = tx.ExecContext(ctx, `UPDATE ops_runs SET mode='PAUSED' WHERE id=$1`, id)
	case "resume":
		_, err = tx.ExecContext(ctx, `UPDATE ops_runs SET mode='ACTIVE' WHERE id=$1`, id)
	case "cancel":
		_, err = tx.ExecContext(ctx, `UPDATE ops_runs SET mode='CANCELLED' WHERE id=$1`, id)
		if err == nil {
			_, err = tx.ExecContext(ctx, `UPDATE ops_tasks SET state='CANCELLED',updated_at=now() WHERE run_id=$1 AND state IN('WAITING','QUEUED','FAILED','BLOCKED')`, id)
		}
	case "retry_failed":
		rows, e := tx.QueryContext(ctx, taskSelect+`WHERE run_id=$1 AND state IN('FAILED','BLOCKED') ORDER BY ordinal FOR UPDATE`, id)
		if e != nil {
			return e
		}
		items := []Task{}
		for rows.Next() {
			t, e := scanTask(rows)
			if e != nil {
				rows.Close()
				return e
			}
			items = append(items, t)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		for _, t := range items {
			next := "QUEUED"
			if t.Spec.ParentID != "" {
				var parent string
				if err = tx.QueryRowContext(ctx, `SELECT state FROM ops_tasks WHERE id=$1`, t.Spec.ParentID).Scan(&parent); err != nil {
					return err
				}
				if parent != "SUCCEEDED" {
					next = "WAITING"
				}
			}
			if _, err = tx.ExecContext(ctx, `UPDATE ops_tasks SET state=$2,generation=generation+1,current_attempt=NULL,error_code='',error_message='',dispatched_at=NULL,queued_at=CASE WHEN $2='QUEUED' THEN now() ELSE NULL END,updated_at=now() WHERE id=$1`, t.ID, next); err != nil {
				return err
			}
			if next == "QUEUED" {
				if err = enqueue(ctx, tx, t.ID, t.Spec.Kind, t.Generation+1); err != nil {
					return err
				}
			}
		}
	default:
		return Invalid("未知操作")
	}
	if err != nil {
		return err
	}
	if err = addEvent(ctx, tx, id, "", "", "info", "operator."+action, actor+"："+action); err != nil {
		return err
	}
	if err = refreshRun(ctx, tx, id); err != nil {
		return err
	}
	return tx.Commit()
}
func (s *Store) Abandon(ctx context.Context, id, actor string) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	t, _, err := lockTask(ctx, tx, id)
	if err != nil {
		return err
	}
	if t.State != "RUNNING" && t.State != "COMMITTING" {
		return Conflict("只有失联执行可以撤销执行权")
	}
	var lease time.Time
	var worker string
	if err = tx.QueryRowContext(ctx, `SELECT lease_until,worker_id FROM ops_attempts WHERE id=$1 FOR UPDATE`, t.CurrentAttempt).Scan(&lease, &worker); err != nil {
		return err
	}
	if time.Now().Before(lease) {
		return Conflict("执行租约尚未过期")
	}
	var fresh bool
	if err = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM ops_workers WHERE id=$1 AND seen_at>now()-interval '75 seconds')`, worker).Scan(&fresh); err != nil {
		return err
	}
	if fresh {
		return Conflict("原Worker仍有新鲜心跳；不能撤销其执行权")
	}
	if _, err = tx.ExecContext(ctx, `UPDATE ops_attempts SET state='SUPERSEDED',finished_at=now(),token_sha256=$2 WHERE id=$1`, t.CurrentAttempt, Digest([]byte(NewID()))); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `UPDATE ops_tasks SET state='FAILED',error_code='WORKER_LOST',error_message='操作员确认原Worker退出后已撤销旧执行权',updated_at=now() WHERE id=$1`, id); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `UPDATE ops_tasks SET state='BLOCKED',error_code='UPSTREAM_FAILED',error_message='上游Worker失联',updated_at=now() WHERE parent_id=$1 AND state='WAITING'`, id); err != nil {
		return err
	}
	if err = addEvent(ctx, tx, t.RunID, id, t.CurrentAttempt, "warning", "operator.abandon", actor+"已确认Worker退出并撤销提交权；未执行OS进程终止"); err != nil {
		return err
	}
	if err = refreshRun(ctx, tx, t.RunID); err != nil {
		return err
	}
	return tx.Commit()
}
func (s *Store) Counts(ctx context.Context) (map[string]int, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT state,count(*) FROM ops_runs GROUP BY state`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]int{}
	for rows.Next() {
		var k string
		var n int
		if err = rows.Scan(&k, &n); err != nil {
			return nil, err
		}
		out[k] = n
	}
	return out, rows.Err()
}

// Legacy queries never infer "computing" from the old published-as-RUNNING flag.
func (s *Store) Legacy(ctx context.Context, q, status string, limit int, before time.Time, id string) ([]json.RawMessage, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT jsonb_build_object('id',j.job_id,'run_id',j.run_id,'trace_id',j.trace_id,'kind',j.job_type,'state',j.status,'config_version',j.config_version,'created_at',j.created_at,'dispatch_legacy_at',j.started_at,'finished_at',j.completed_at,'runtime_ms',a.metadata->'runtime_ms','error_code',a.error_code,'error_message',a.error_message,'timing_semantics','legacy_dispatch_not_execution','source','automatic') FROM jobs j LEFT JOIN LATERAL (SELECT error_code,error_message,metadata FROM job_attempts WHERE job_id=j.job_id ORDER BY attempt_no DESC LIMIT 1) a ON true WHERE ($1='' OR position(lower($1) in lower(j.job_id::text||' '||j.run_id::text||' '||j.trace_id::text||' '||j.job_type||' '||j.config_version))>0) AND ($2='' OR j.status=$2) AND ($3::timestamptz IS NULL OR (j.created_at,j.job_id)<($3,$4::uuid)) ORDER BY j.created_at DESC,j.job_id DESC LIMIT $5`, q, status, nullableTime(before), nullableUUID(id), limit+1)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []json.RawMessage{}
	for rows.Next() {
		var raw []byte
		if err = rows.Scan(&raw); err != nil {
			return nil, err
		}
		items = append(items, json.RawMessage(RedactJSON(raw)))
	}
	return items, rows.Err()
}
func (s *Store) LegacyDetail(ctx context.Context, id string) (json.RawMessage, error) {
	var raw []byte
	err := s.DB.QueryRowContext(ctx, `SELECT jsonb_build_object('id',j.job_id,'run_id',j.run_id,'trace_id',j.trace_id,'kind',j.job_type,'state',j.status,'config_version',j.config_version,'created_at',j.created_at,'dispatch_legacy_at',j.started_at,'finished_at',j.completed_at,'runtime_ms',a.metadata->'runtime_ms','error_code',a.error_code,'error_message',a.error_message,'request',j.request_payload,'timing_semantics','legacy_dispatch_not_execution','attempts',COALESCE((SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attempt_no) FROM job_attempts a WHERE a.job_id=j.job_id),'[]'::jsonb),'outbox',COALESCE((SELECT jsonb_agg(jsonb_build_object('state',o.status,'subject',o.subject,'created_at',o.created_at,'published_at',o.published_at,'error',o.last_error)) FROM outbox_events o WHERE o.aggregate_type='job' AND o.aggregate_id=j.job_id::text),'[]'::jsonb)) FROM jobs j LEFT JOIN LATERAL (SELECT error_code,error_message,metadata FROM job_attempts WHERE job_id=j.job_id ORDER BY attempt_no DESC LIMIT 1) a ON true WHERE j.job_id=$1`, id).Scan(&raw)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return RedactJSON(raw), err
}
func (s *Store) Inventory(ctx context.Context, start, end time.Time, radar string, limit int, before time.Time, id string) ([]json.RawMessage, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT jsonb_build_object('id',s.scan_id,'radar_id',s.radar_id,'observed_at',s.volume_end_time,'state',r.status,'normalized_uri',r.normalized_uri,'qc_uri',r.qc_uri,'grid_uri',r.grid_uri,'config_version',r.radar_config_version,'verification','catalog_only') FROM radar_scans s JOIN radar_scan_runs r ON r.scan_id=s.scan_id WHERE s.volume_end_time >= $1 AND s.volume_end_time < $2 AND ($3='' OR lower(s.radar_id)=lower($3)) AND ($4::timestamptz IS NULL OR (s.volume_end_time,s.scan_id)<($4,$5::uuid)) ORDER BY s.volume_end_time DESC,s.scan_id DESC LIMIT $6`, start, end, radar, nullableTime(before), nullableUUID(id), limit+1)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []json.RawMessage{}
	for rows.Next() {
		var raw []byte
		if err = rows.Scan(&raw); err != nil {
			return nil, err
		}
		out = append(out, raw)
	}
	return out, rows.Err()
}

// Wake re-enqueues a bounded page of unclaimed tasks if broker retention expired.
// It is an explicit operator repair; generation/claim CAS makes old refs harmless.
func (s *Store) Wake(ctx context.Context, id string) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = storageAdmission(ctx, tx, true); err != nil {
		return err
	}
	if err = liveStorageRun(ctx, tx, id); err != nil {
		return err
	}
	var mode string
	if err = tx.QueryRowContext(ctx, `SELECT mode FROM ops_runs WHERE id=$1 FOR UPDATE`, id).Scan(&mode); err != nil {
		return err
	}
	if mode != "ACTIVE" {
		return Conflict("作业未处于接单状态")
	}
	rows, err := tx.QueryContext(ctx, `SELECT id::text,kind,generation FROM ops_tasks WHERE run_id=$1 AND state='QUEUED' ORDER BY ordinal LIMIT 128`, id)
	if err != nil {
		return err
	}
	type item struct {
		id, kind string
		g        int
	}
	items := []item{}
	for rows.Next() {
		var v item
		if err = rows.Scan(&v.id, &v.kind, &v.g); err != nil {
			rows.Close()
			return err
		}
		items = append(items, v)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, v := range items {
		if err = enqueue(ctx, tx, v.id, v.kind, v.g); err != nil {
			return err
		}
	}
	if err = addEvent(ctx, tx, id, "", "", "info", "operator.wake", fmt.Sprintf("重新投递%d个尚未领取的引用；不会重复领取已执行任务", len(items))); err != nil {
		return err
	}
	return tx.Commit()
}

func metricJSON(m map[string]float64) []byte {
	if m == nil {
		return []byte(`{}`)
	}
	return JSON(m)
}

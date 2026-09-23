package operations

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"
)

type Pool struct {
	Kind           string     `json:"kind"`
	Mode           string     `json:"mode"`
	Revision       int64      `json:"revision"`
	UpdatedAt      time.Time  `json:"updated_at"`
	Actor          string     `json:"actor"`
	Reason         string     `json:"reason"`
	Registered     int        `json:"registered"`
	Fresh          int        `json:"fresh"`
	Ready          int        `json:"ready"`
	Active         int        `json:"active"`
	Stalled        int        `json:"stalled"`
	Queued         int        `json:"queued"`
	OldestQueuedAt *time.Time `json:"oldest_queued_at"`
}
type PoolAction struct {
	Action           string `json:"action"`
	ExpectedRevision int64  `json:"expected_revision"`
	Reason           string `json:"reason"`
}

func (a PoolAction) Validate() error {
	if (a.Action != "drain" && a.Action != "resume") || a.ExpectedRevision < 1 || len([]rune(strings.TrimSpace(a.Reason))) < 1 || len([]rune(a.Reason)) > 256 {
		return Invalid("请选择排空或恢复，并填写操作原因和当前配置版本")
	}
	return nil
}
func (s *Store) Pools(ctx context.Context) ([]Pool, error) {
	rows, e := s.DB.QueryContext(ctx, `SELECT p.kind,p.mode,p.revision,p.updated_at,p.actor,p.reason,
 (SELECT count(*) FROM ops_workers w WHERE w.kind=p.kind),
 (SELECT count(*) FROM ops_workers w WHERE w.kind=p.kind AND w.seen_at>now()-interval '75 seconds'),
 (SELECT count(*) FROM ops_workers w WHERE w.kind=p.kind AND w.ready AND w.seen_at>now()-interval '75 seconds'),
 (SELECT count(*) FROM ops_tasks t WHERE t.kind=p.kind AND t.state IN ('RUNNING','COMMITTING')),
 (SELECT count(*) FROM ops_tasks t JOIN ops_attempts a ON a.id=t.current_attempt WHERE t.kind=p.kind AND t.state IN('RUNNING','COMMITTING') AND a.lease_until<now()),
 (SELECT count(*) FROM ops_tasks t JOIN ops_runs r ON r.id=t.run_id WHERE t.kind=p.kind AND t.state='QUEUED' AND r.mode='ACTIVE'),
 (SELECT min(t.queued_at) FROM ops_tasks t JOIN ops_runs r ON r.id=t.run_id WHERE t.kind=p.kind AND t.state='QUEUED' AND r.mode='ACTIVE')
 FROM ops_pool_controls p ORDER BY p.kind`)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []Pool{}
	for rows.Next() {
		var p Pool
		if e = rows.Scan(&p.Kind, &p.Mode, &p.Revision, &p.UpdatedAt, &p.Actor, &p.Reason, &p.Registered, &p.Fresh, &p.Ready, &p.Active, &p.Stalled, &p.Queued, &p.OldestQueuedAt); e != nil {
			return nil, e
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
func (s *Store) PoolMode(ctx context.Context, kind string) (string, error) {
	var mode string
	e := s.DB.QueryRowContext(ctx, `SELECT mode FROM ops_pool_controls WHERE kind=$1`, kind).Scan(&mode)
	return mode, e
}
func (s *Store) PoolAction(ctx context.Context, kind string, a PoolAction, actor string) error {
	if !validKind(kind) {
		return Invalid("执行池不存在")
	}
	if e := a.Validate(); e != nil {
		return e
	}
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	// Only the pool row is locked here. Claim holds a shared lock through commit,
	// so after this transaction returns no later claim can bypass the pause.
	var revision int64
	var mode string
	if e = tx.QueryRowContext(ctx, `SELECT revision,mode FROM ops_pool_controls WHERE kind=$1 FOR UPDATE`, kind).Scan(&revision, &mode); errors.Is(e, sql.ErrNoRows) {
		return ErrNotFound
	} else if e != nil {
		return e
	}
	if revision != a.ExpectedRevision {
		return Conflict("执行池配置已变化，请刷新后重试")
	}
	target := "DRAINING"
	if a.Action == "resume" {
		target = "ACCEPTING"
	}
	if mode == target {
		return nil
	}
	reason := Redact(strings.TrimSpace(a.Reason))
	_, e = tx.ExecContext(ctx, `UPDATE ops_pool_controls SET mode=$2,revision=revision+1,updated_at=now(),actor=$3,reason=$4 WHERE kind=$1`, kind, target, actor, reason)
	if e != nil {
		return e
	}
	_, e = tx.ExecContext(ctx, `INSERT INTO ops_resource_events(kind,action,revision,actor,reason) VALUES($1,$2,$3,$4,$5)`, kind, a.Action, revision+1, actor, reason)
	if e != nil {
		return e
	}
	return tx.Commit()
}

package operations

import (
	"context"
	"database/sql"
	"strings"
	"time"
)

func (s *Store) ReleaseChannels(ctx context.Context) ([]ReleaseChannel, error) {
	rows, e := s.DB.QueryContext(ctx, `SELECT kind,current_fingerprint,previous_fingerprint,revision,updated_at FROM ops_release_channels ORDER BY kind`)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []ReleaseChannel{}
	for rows.Next() {
		var c ReleaseChannel
		if e = rows.Scan(&c.Kind, &c.Current, &c.Previous, &c.Revision, &c.UpdatedAt); e != nil {
			return nil, e
		}
		out = append(out, c)
	}
	return out, rows.Err()
}
func (s *Store) ReleaseCatalog(ctx context.Context) (map[string]any, error) {
	channels, e := s.ReleaseChannels(ctx)
	if e != nil {
		return nil, e
	}
	rows, e := s.DB.QueryContext(ctx, `WITH identities AS (
 SELECT kind,fingerprint,identity,seen_at AS at FROM ops_workers
 UNION ALL SELECT kind,spec#>>'{identity,fingerprint}',spec->'identity',created_at FROM ops_tasks
 ), latest AS (SELECT DISTINCT ON(kind,fingerprint) kind,fingerprint,identity,at FROM identities ORDER BY kind,fingerprint,at DESC)
 SELECT jsonb_build_object('kind',l.kind,'fingerprint',l.fingerprint,'identity',l.identity,'last_used_at',l.at,
 'fresh_workers',(SELECT count(*) FROM ops_workers w WHERE w.kind=l.kind AND w.fingerprint=l.fingerprint AND w.ready AND w.seen_at>now()-interval '75 seconds'),
 'pending_tasks',(SELECT count(*) FROM ops_tasks t WHERE t.spec#>>'{identity,fingerprint}'=l.fingerprint AND t.state IN('WAITING','QUEUED','RUNNING','COMMITTING')))
 FROM latest l LEFT JOIN ops_release_channels c ON c.kind=l.kind
 ORDER BY (l.fingerprint IN(c.current_fingerprint,c.previous_fingerprint)) DESC NULLS LAST,l.at DESC LIMIT 200`)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	items, e := rawRows(rows)
	return map[string]any{"channels": channels, "items": items, "limit": 200, "scope": "management_execution_identity", "sampled_at": time.Now().UTC()}, e
}

type ReleaseChoice struct {
	Fingerprint      string `json:"fingerprint"`
	ExpectedRevision int64  `json:"expected_revision"`
	Reason           string `json:"reason"`
}

func (s *Store) ChooseRelease(ctx context.Context, kind string, a ReleaseChoice) error {
	if !validKind(kind) || !shaPattern.MatchString(a.Fingerprint) || a.ExpectedRevision < 1 || strings.TrimSpace(a.Reason) == "" || len(a.Reason) > 512 {
		return Invalid("版本选择及原因非法")
	}
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	if e = storageAdmission(ctx, tx, false); e != nil {
		return e
	}
	var current string
	var rev int64
	if e = tx.QueryRowContext(ctx, `SELECT current_fingerprint,revision FROM ops_release_channels WHERE kind=$1 FOR UPDATE`, kind).Scan(&current, &rev); e != nil {
		return e
	}
	if rev != a.ExpectedRevision {
		return Conflict("当前版本选择已改变，请刷新")
	}
	var fresh bool
	if e = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM ops_workers WHERE kind=$1 AND fingerprint=$2 AND ready AND seen_at>now()-interval '75 seconds')`, kind, a.Fingerprint).Scan(&fresh); e != nil {
		return e
	}
	if !fresh {
		return Conflict("没有该身份的新鲜就绪Worker；此操作不会自动部署镜像或替换配置")
	}
	if current == a.Fingerprint {
		return nil
	}
	_, e = tx.ExecContext(ctx, `UPDATE ops_release_channels SET previous_fingerprint=current_fingerprint,current_fingerprint=$2,revision=revision+1,actor='administrator',reason=$3,updated_at=now() WHERE kind=$1`, kind, a.Fingerprint, Redact(a.Reason))
	if e != nil {
		return e
	}
	if e = storageEvent(ctx, tx, "release.selected", "administrator", map[string]any{"kind": kind, "previous": current, "current": a.Fingerprint, "reason": Redact(a.Reason)}); e != nil {
		return e
	}
	return tx.Commit()
}

// Selection affects new preflight plans only. Already frozen attempts retain
// their identity and are never silently upgraded to a new algorithm.
func PreferredIdentity(expected Identity, channels []ReleaseChannel) Identity {
	for _, c := range channels {
		if c.Kind == expected.Kind && c.Current != "" {
			expected.Fingerprint = c.Current
			break
		}
	}
	return expected
}
func validateReleaseSelection(ctx context.Context, tx *sql.Tx, specs []Spec) error {
	for _, spec := range specs {
		var current string
		if e := tx.QueryRowContext(ctx, `SELECT current_fingerprint FROM ops_release_channels WHERE kind=$1 FOR SHARE`, spec.Kind).Scan(&current); e != nil {
			return e
		}
		if current != "" && spec.Identity.Fingerprint != current {
			return Conflict("管理默认版本已改变，请重新预检查；已有作业不变")
		}
	}
	return nil
}

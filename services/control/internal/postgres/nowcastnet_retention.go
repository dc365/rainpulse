package postgres

import (
	"context"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Keep audit rows, reclaim only superseded large NowcastNet output bundles.
func (store *Store) PruneNowcastNet(ctx context.Context, validate func(context.Context, string) error, remove func(context.Context, string) error) (int, error) {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return 0, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	var locked bool
	if err = tx.QueryRow(ctx, `SELECT pg_try_advisory_xact_lock(hashtextextended('nowcastnet-product-retention',0))`).Scan(&locked); err != nil || !locked {
		return 0, err
	}
	rows, err := tx.Query(ctx, `
 SELECT old.algorithm_run_id, old.output_uri, current.output_uri
 FROM algorithm_runs old JOIN forecast_runs f ON f.run_id=old.run_id
 JOIN LATERAL (
   SELECT a.algorithm_run_id,a.output_uri,a.completed_at FROM algorithm_runs a
   JOIN forecast_runs r ON r.run_id=a.run_id
   WHERE r.grid_id=f.grid_id AND r.issue_time=f.issue_time AND a.algorithm_id=old.algorithm_id
     AND a.status='completed' AND a.output_uri IS NOT NULL
   ORDER BY a.completed_at DESC,a.algorithm_run_id DESC LIMIT 1
 ) current ON current.algorithm_run_id<>old.algorithm_run_id
 WHERE old.algorithm_id='nowcastnet' AND old.status='completed' AND old.output_uri IS NOT NULL
   AND old.output_uri<>current.output_uri
   AND COALESCE(old.diagnostics->>'products_pruned','false')<>'true'
   AND current.completed_at < CURRENT_TIMESTAMP - INTERVAL '1 minute'
   AND NOT EXISTS (SELECT 1 FROM algorithm_runs active JOIN forecast_runs r ON r.run_id=active.run_id
      WHERE r.grid_id=f.grid_id AND r.issue_time=f.issue_time AND active.algorithm_id='nowcastnet' AND active.status='running')
 ORDER BY old.completed_at LIMIT 10 FOR UPDATE OF old SKIP LOCKED`)
	if err != nil {
		return 0, err
	}
	type item struct {
		id           uuid.UUID
		old, current string
	}
	items := []item{}
	for rows.Next() {
		var v item
		if err = rows.Scan(&v.id, &v.old, &v.current); err != nil {
			rows.Close()
			return 0, err
		}
		items = append(items, v)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return 0, err
	}
	for _, v := range items {
		if err = validate(ctx, v.current); err != nil {
			return 0, err
		}
		if err = remove(ctx, v.old); err != nil {
			return 0, err
		}
		if _, err = tx.Exec(ctx, `UPDATE algorithm_runs SET diagnostics=jsonb_set(COALESCE(diagnostics,'{}'::jsonb),'{products_pruned}','true'::jsonb) WHERE algorithm_run_id=$1`, v.id); err != nil {
			return 0, err
		}
	}
	return len(items), tx.Commit(ctx)
}

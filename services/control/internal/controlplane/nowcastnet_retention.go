package controlplane

import (
	"context"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/objectstore"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"log/slog"
	"time"
)

func nowcastNetRetentionLoop(ctx context.Context, store *postgresstore.Store) {
	reader, err := objectstore.NewFromEnvironment()
	if err != nil {
		slog.Warn("NowcastNet retention unavailable", "error", err)
		return
	}
	ticker := time.NewTicker(time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		check, cancel := context.WithTimeout(ctx, 45*time.Second)
		n, err := store.PruneNowcastNet(check, func(ctx context.Context, uri string) error {
			_, _, err := reader.Read(ctx, uri, "manifest.json")
			return err
		}, reader.DeleteNowcastNetBundle)
		cancel()
		if err != nil {
			slog.Warn("NowcastNet retention retry", "error", err)
		} else if n > 0 {
			slog.Info("pruned superseded NowcastNet bundles", "count", n)
		}
	}
}

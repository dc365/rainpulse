package controlplane

import (
	"context"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/messaging"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/runtimeconfig"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Run owns one subscription/planner lifetime. Callers must wait for it to exit
// before retrying, so no planner is duplicated within this process.
func Run(ctx context.Context, pool *pgxpool.Pool) error {
	bus, err := messaging.Connect(runtimeconfig.NATSURL(), "rainpulse")
	if err != nil {
		return err
	}
	defer bus.Close()
	if err = bus.Ensure(ctx); err != nil {
		return err
	}
	store := postgresstore.New(pool)
	return serve(ctx, store, bus, orchestration.NewService(store, orchestration.Options{}))
}

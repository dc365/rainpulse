package controlplane

import (
	"context"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/messaging"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workloads"
)

func configureResourceRouting(bus *messaging.JetStream, store *postgresstore.Store) error {
	settings, err := workloads.SettingsFromEnvironment()
	if err != nil {
		return err
	}
	if settings.Enabled {
		bus.SetRequestRouter(func(ctx context.Context, event workflow.OutboxEvent) (workflow.OutboxEvent, error) {
			return store.RouteRequestedEvent(ctx, event, settings)
		})
	}
	return nil
}

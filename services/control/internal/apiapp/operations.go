package apiapp

import (
	"net/http"
	"os"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/controlplane"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operations"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/releaseguard"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/jackc/pgx/v5/stdlib"
)

// OpenDBFromPool shares the existing pgx pool and keeps no idle SQL connections.
// Worker telemetry bypasses only admission pause, not its own authentication.
func withOperations(next http.Handler, pool *pgxpool.Pool, store *postgresstore.Store, objects operations.ObjectReader, adminToken string) http.Handler {
	db := stdlib.OpenDBFromPool(pool)
	service := &operations.Service{Store: &operations.Store{DB: db}, Builder: controlplane.NewOperationsBuilder(store, db), Objects: objects}
	return operations.NewHandler(service, next, operations.HTTPOptions{AdminToken: adminToken, WorkerToken: os.Getenv("RAINPULSE_OPS_WORKER_TOKEN"), Admit: releaseguard.Acquire, LokiURL: os.Getenv("RAINPULSE_OPS_LOKI_URL")})
}

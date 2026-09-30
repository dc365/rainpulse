package apiapp

import (
	"log/slog"
	"net/http"
	"os"
	"strings"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/controlplane"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operations"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/releaseguard"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/jackc/pgx/v5/stdlib"
)

// tiandituToken resolves the basemap tile key: RAINPULSE_TIANDITU_TOKEN env
// first, then a token file (RAINPULSE_TIANDITU_TOKEN_FILE, default
// deploy/tianditu-token relative to the working directory). The file fallback
// exists because the systemd EnvironmentFile is root-owned while the service
// itself runs as the deploy user.
func tiandituToken() string {
	if token := strings.TrimSpace(os.Getenv("RAINPULSE_TIANDITU_TOKEN")); token != "" {
		return token
	}
	path := strings.TrimSpace(os.Getenv("RAINPULSE_TIANDITU_TOKEN_FILE"))
	if path == "" {
		path = "deploy/tianditu-token"
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// OpenDBFromPool shares the existing pgx pool and keeps no idle SQL connections.
// Worker telemetry bypasses only admission pause, not its own authentication.
func withOperations(next http.Handler, pool *pgxpool.Pool, store *postgresstore.Store, objects operations.ObjectReader, adminToken string) http.Handler {
	db := stdlib.OpenDBFromPool(pool)
	service := &operations.Service{Store: &operations.Store{DB: db}, Builder: controlplane.NewOperationsBuilder(store, db), Objects: objects}
	adminAuthMode := strings.TrimSpace(os.Getenv("RAINPULSE_ADMIN_AUTH_MODE"))
	if adminAuthMode == operations.AdminAuthModeValidation {
		slog.Warn("RainPulse admin credential check is disabled in validation mode; keep the management endpoint on a trusted network")
	}
	return operations.NewHandler(service, next, operations.HTTPOptions{
		AdminToken:        adminToken,
		AdminAuthMode:     adminAuthMode,
		WorkerToken:       os.Getenv("RAINPULSE_OPS_WORKER_TOKEN"),
		Admit:             releaseguard.Acquire,
		LokiURL:           os.Getenv("RAINPULSE_OPS_LOKI_URL"),
		TiandituToken:     tiandituToken(),
		BasemapConfigPath: strings.TrimSpace(os.Getenv("RAINPULSE_BASEMAP_CONFIG_FILE")),
	})
}

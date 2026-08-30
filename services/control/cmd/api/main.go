package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/alerting"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/api"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/buildinfo"
	ensembleproductstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/ensembleproducts"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/healthcheck"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/objectstore"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/runtimeconfig"
	verificationstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/verification"
	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	address := environmentOrDefault("RAINPULSE_HTTP_ADDR", ":8080")
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		if err := healthcheck.RunJSONStatus(environmentOrDefault("RAINPULSE_API_HEALTH_URL", "http://127.0.0.1:8080/api/v1/system/status"), "ready"); err != nil {
			slog.Error("control API healthcheck failed", "error", err)
			os.Exit(1)
		}
		return
	}

	version := os.Getenv("RAINPULSE_VERSION")
	if version == "" {
		version = buildinfo.Identity()
	}
	databaseURL, err := runtimeconfig.DatabaseURL()
	if err != nil {
		slog.Error("configure control database", "error", err)
		os.Exit(1)
	}
	pool, err := pgxpool.New(context.Background(), databaseURL)
	if err != nil {
		slog.Error("open control database", "error", err)
		os.Exit(1)
	}
	defer pool.Close()
	startupContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := pool.Ping(startupContext); err != nil {
		slog.Error("ping control database", "error", err)
		os.Exit(1)
	}
	store := postgresstore.New(pool)
	commands := orchestration.NewService(store, orchestration.Options{})
	diagnosticLayers, err := objectstore.NewFromEnvironment()
	if err != nil {
		slog.Error("configure diagnostic object reader", "error", err)
		os.Exit(1)
	}
	verificationReports := verificationstore.NewFileStore(environmentOrDefault(
		"RAINPULSE_ALGORITHM_VERIFICATION_ROOT",
		"/var/lib/rainpulse/algorithm-verification",
	))
	ensembleProducts := ensembleproductstore.NewFileStore(environmentOrDefault(
		"RAINPULSE_ENSEMBLE_PRODUCT_ROOT",
		"/var/lib/rainpulse/ensemble-products",
	))
	alertReader, err := alerting.NewClient(
		environmentOrDefault("RAINPULSE_PROMETHEUS_URL", "http://prometheus:9090"),
		environmentOrDefault("RAINPULSE_ALERTMANAGER_URL", "http://alertmanager:9093"),
		alerting.Options{},
	)
	if err != nil {
		slog.Error("configure alert readers", "error", err)
		os.Exit(1)
	}

	server := &http.Server{
		Addr: address,
		Handler: api.NewHandler(api.Options{
			Version:           version,
			AdminToken:        os.Getenv("RAINPULSE_ADMIN_TOKEN"),
			Runs:              store,
			Observations:      store,
			Commands:          commands,
			DiagnosticLayers:  diagnosticLayers,
			Products:          store,
			ProductObjects:    diagnosticLayers,
			Verification:      verificationReports,
			EnsembleProducts:  ensembleProducts,
			Metrics:           store,
			Alerts:            alertReader,
			OperationalIssues: store,
		}),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      0,
		IdleTimeout:       60 * time.Second,
	}

	slog.Info("starting RainPulse control API", "address", address, "version", version)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("control API stopped", "error", err)
		os.Exit(1)
	}
}

func environmentOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

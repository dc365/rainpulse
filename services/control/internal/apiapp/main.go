package apiapp

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/bdpruntime"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/buildinfo"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/healthcheck"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/runtimeconfig"
	"github.com/jackc/pgx/v5/pgxpool"
)

func Main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		if err := healthcheck.RunJSONStatus(environmentOrDefault("RAINPULSE_API_HEALTH_URL", "http://127.0.0.1:8080/api/v1/system/status"), "ready"); err != nil {
			slog.Error("control API healthcheck failed", "error", err)
			os.Exit(1)
		}
		return
	}
	if _, err := bdpruntime.Prepare(bdpruntime.ComponentAPI, false); err != nil {
		slog.Error("initialize Ruiyun BDP runtime for control API", "error", err)
		os.Exit(1)
	}
	address := environmentOrDefault("RAINPULSE_HTTP_ADDR", ":8080")

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
	handler, err := NewHandler(pool)
	if err != nil {
		slog.Error("configure API", "error", err)
		os.Exit(1)
	}
	server := &http.Server{
		Addr:              address,
		Handler:           handler,
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

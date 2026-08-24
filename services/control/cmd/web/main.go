package main

import (
	"errors"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/healthcheck"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/webgateway"
)

func main() {
	address := environmentOrDefault("RAINPULSE_WEB_ADDR", ":8080")
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		if err := healthcheck.Run(environmentOrDefault("RAINPULSE_WEB_HEALTH_URL", "http://127.0.0.1:8080/healthz")); err != nil {
			slog.Error("web gateway healthcheck failed", "error", err)
			os.Exit(1)
		}
		return
	}

	handler, err := webgateway.NewHandler(webgateway.Options{
		WebRoot:    environmentOrDefault("RAINPULSE_WEB_ROOT", "/srv/rainpulse-web"),
		APIBaseURL: environmentOrDefault("RAINPULSE_API_BASE_URL", "http://api:8080"),
	})
	if err != nil {
		slog.Error("configure RainPulse web gateway", "error", err)
		os.Exit(1)
	}

	server := &http.Server{
		Addr:              address,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	slog.Info("starting RainPulse web gateway", "address", address)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("web gateway stopped", "error", err)
		os.Exit(1)
	}
}

func environmentOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

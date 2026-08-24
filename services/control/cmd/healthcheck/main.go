package main

import (
	"log/slog"
	"os"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/healthcheck"
)

func main() {
	target := os.Getenv("RAINPULSE_HEALTHCHECK_URL")
	if len(os.Args) > 1 {
		target = os.Args[1]
	}
	if target == "" {
		slog.Error("healthcheck URL is required")
		os.Exit(2)
	}

	if err := healthcheck.Run(target); err != nil {
		slog.Error("healthcheck failed", "target", target, "error", err)
		os.Exit(1)
	}
}

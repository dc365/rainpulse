package main

import (
	"context"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/unified"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	if err := unified.Run(ctx); err != nil {
		slog.Error("RainPulse stopped", "error", err)
		os.Exit(1)
	}
}

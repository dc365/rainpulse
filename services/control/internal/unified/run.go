package unified

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/apiapp"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/bdpruntime"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/controlplane"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/ingestapp"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/runtimeconfig"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/webgateway"
	"github.com/jackc/pgx/v5/pgxpool"
)

func retry(ctx context.Context, delay time.Duration, run func(context.Context) error, report func(error)) {
	for ctx.Err() == nil {
		err := run(ctx)
		if ctx.Err() != nil {
			return
		}
		report(err)
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

func Run(ctx context.Context) error {
	platform, err := bdpruntime.Prepare(bdpruntime.ComponentOrchestrator, true)
	if err != nil {
		return err
	}
	// A process has one environment: reject incompatible component overrides.
	merged := map[string]string{}
	for _, component := range []bdpruntime.Component{bdpruntime.ComponentAPI, bdpruntime.ComponentWeb, bdpruntime.ComponentIngest, bdpruntime.ComponentOrchestrator} {
		for key, value := range platform.Config.Environment[string(component)] {
			if old, ok := merged[key]; ok && old != value {
				return fmt.Errorf("conflicting BDP component setting %s", key)
			}
			merged[key] = value
		}
	}
	for key, value := range merged {
		if err := os.Setenv(key, value); err != nil {
			return err
		}
	}
	// The manifest-based ingest module is the sole scanner in unified mode.
	if err := os.Setenv("RAINPULSE_RADAR_INGEST_ENABLED", "false"); err != nil {
		return err
	}
	url, err := runtimeconfig.DatabaseURL()
	if err != nil {
		return err
	}
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		return err
	}
	defer pool.Close()
	startup, cancelStartup := context.WithTimeout(ctx, 5*time.Second)
	err = pool.Ping(startup)
	cancelStartup()
	if err != nil {
		return err
	}
	api, err := apiapp.NewHandler(pool)
	if err != nil {
		return err
	}
	handler, err := webgateway.NewHandler(webgateway.Options{APIHandler: api, WebRoot: env("RAINPULSE_WEB_ROOT", "apps/web/dist"), AdminToken: os.Getenv("RAINPULSE_ADMIN_TOKEN")})
	if err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(ctx)
	var tasks sync.WaitGroup
	defer func() { cancel(); tasks.Wait() }()
	launch := func(name string, run func(context.Context) error) {
		tasks.Add(1)
		go func() {
			defer tasks.Done()
			retry(ctx, 5*time.Second, run, func(err error) { slog.Error("background module stopped; retrying", "module", name, "error", err) })
		}()
	}
	store := postgresstore.New(pool)
	commands := orchestration.NewService(store, orchestration.Options{})
	launch("ingest", func(ctx context.Context) error { return ingestapp.Run(ctx, platform, commands) })
	launch("orchestrator", func(ctx context.Context) error { return controlplane.Run(ctx, pool) })
	server := &http.Server{Addr: env("RAINPULSE_WEB_ADDR", ":4173"), Handler: handler, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}
	// Local compatibility for existing GPU workers/scripts; never expose the
	// unfiltered internal API on a public address.
	compat := &http.Server{Addr: "127.0.0.1:8080", Handler: api, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}
	errorsCh := make(chan error, 1)
	go func() {
		err := compat.ListenAndServe()
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			errorsCh <- err
			cancel()
		}
	}()
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		<-ctx.Done()
		shutdown, done := context.WithTimeout(context.Background(), 10*time.Second)
		defer done()
		_ = server.Shutdown(shutdown)
		_ = compat.Shutdown(shutdown)
	}()
	slog.Info("RainPulse unified process starting", "address", server.Addr)
	err = server.ListenAndServe()
	cancel()
	<-stopped
	select {
	case compatibilityError := <-errorsCh:
		return compatibilityError
	default:
	}
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

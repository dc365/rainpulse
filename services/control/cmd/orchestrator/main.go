package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/messaging"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/runtimeconfig"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	pool, store, bus, service, err := dependencies(ctx)
	if err != nil {
		slog.Error("initialize RainPulse orchestrator", "error", err)
		os.Exit(1)
	}
	defer pool.Close()
	defer bus.Close()

	command := "serve"
	if len(os.Args) > 1 {
		command = os.Args[1]
	}
	switch command {
	case "serve":
		if err := serve(ctx, store, bus, service); err != nil && !errors.Is(err, context.Canceled) {
			slog.Error("RainPulse orchestrator stopped", "error", err)
			os.Exit(1)
		}
	case "simulate":
		if err := simulate(ctx, service, false); err != nil {
			slog.Error("create simulated run", "error", err)
			os.Exit(1)
		}
	case "simulate-failure":
		if err := simulate(ctx, service, true); err != nil {
			slog.Error("create simulated failure run", "error", err)
			os.Exit(1)
		}
	case "complete":
		if len(os.Args) != 3 {
			slog.Error("complete requires a job UUID")
			os.Exit(2)
		}
		if err := complete(ctx, store, bus, service, os.Args[2]); err != nil {
			slog.Error("publish simulated completion", "error", err)
			os.Exit(1)
		}
	case "replay":
		if len(os.Args) != 3 {
			slog.Error("replay requires a job UUID")
			os.Exit(2)
		}
		if err := replay(ctx, store, bus, os.Args[2]); err != nil {
			slog.Error("replay job request", "error", err)
			os.Exit(1)
		}
	default:
		slog.Error("unknown orchestrator command", "command", command)
		os.Exit(2)
	}
}

func dependencies(ctx context.Context) (*pgxpool.Pool, *postgresstore.Store, *messaging.JetStream, *orchestration.Service, error) {
	databaseURL, err := runtimeconfig.DatabaseURL()
	if err != nil {
		return nil, nil, nil, nil, err
	}
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, nil, nil, nil, fmt.Errorf("open PostgreSQL pool: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, nil, nil, nil, fmt.Errorf("ping PostgreSQL: %w", err)
	}
	store := postgresstore.New(pool)
	bus, err := messaging.Connect(runtimeconfig.NATSURL(), "rainpulse-orchestrator")
	if err != nil {
		pool.Close()
		return nil, nil, nil, nil, err
	}
	if err := bus.Ensure(ctx); err != nil {
		bus.Close()
		pool.Close()
		return nil, nil, nil, nil, err
	}
	return pool, store, bus, orchestration.NewService(store, orchestration.Options{}), nil
}

func serve(ctx context.Context, store *postgresstore.Store, bus *messaging.JetStream, service *orchestration.Service) error {
	healthServer := &http.Server{
		Addr:              environmentOrDefault("RAINPULSE_ORCHESTRATOR_HEALTH_ADDR", ":8090"),
		ReadHeaderTimeout: 3 * time.Second,
		Handler: http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
			if request.URL.Path != "/healthz" {
				http.NotFound(response, request)
				return
			}
			checkCtx, cancel := context.WithTimeout(request.Context(), 2*time.Second)
			defer cancel()
			if !bus.Healthy() || store.Ping(checkCtx) != nil {
				http.Error(response, "unavailable", http.StatusServiceUnavailable)
				return
			}
			response.WriteHeader(http.StatusOK)
			_, _ = response.Write([]byte("ok\n"))
		}),
	}
	serverErrors := make(chan error, 1)
	go func() {
		if err := healthServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErrors <- err
		}
	}()
	go dispatchLoop(ctx, service, bus)
	consumerErrors := make(chan error, 1)
	go func() {
		consumerErrors <- bus.ConsumeResults(ctx, service.HandleResult)
	}()

	slog.Info("RainPulse orchestrator ready")
	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return healthServer.Shutdown(shutdownCtx)
	case err := <-serverErrors:
		return fmt.Errorf("orchestrator health server: %w", err)
	case err := <-consumerErrors:
		return err
	}
}

func dispatchLoop(ctx context.Context, service *orchestration.Service, publisher orchestration.Publisher) {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	for {
		dispatched, err := service.DispatchOnce(ctx, publisher)
		if err != nil {
			slog.Error("dispatch outbox event", "error", err)
		}
		if dispatched {
			continue
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func simulate(ctx context.Context, service *orchestration.Service, forceFailure bool) error {
	issueTime := time.Now().UTC().Truncate(5 * time.Minute)
	var run workflow.Run
	var job workflow.Job
	var err error
	if forceFailure {
		run, job, err = service.CreateFailureSimulation(ctx, issueTime)
	} else {
		run, job, err = service.CreateSimulation(ctx, issueTime)
	}
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"run_id": run.ID.String(),
		"job_id": job.ID.String(),
	})
}

func complete(ctx context.Context, store *postgresstore.Store, bus *messaging.JetStream, service *orchestration.Service, rawJobID string) error {
	jobID, err := uuid.Parse(rawJobID)
	if err != nil {
		return fmt.Errorf("parse job UUID: %w", err)
	}
	job, err := store.GetJob(ctx, jobID)
	if err != nil {
		return err
	}
	event := service.BuildSimulationCompletion(job)
	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("encode completion event: %w", err)
	}
	return bus.Publish(ctx, workflow.OutboxEvent{
		ID:      event.EventID,
		Subject: orchestration.JobCompletedSubject,
		Payload: payload,
	})
}

func replay(ctx context.Context, store *postgresstore.Store, bus *messaging.JetStream, rawJobID string) error {
	jobID, err := uuid.Parse(rawJobID)
	if err != nil {
		return fmt.Errorf("parse job UUID: %w", err)
	}
	job, err := store.GetJob(ctx, jobID)
	if err != nil {
		return err
	}
	var event orchestration.JobRequested
	if err := json.Unmarshal(job.RequestPayload, &event); err != nil {
		return fmt.Errorf("decode job request: %w", err)
	}
	event.EventID = uuid.New()
	event.OccurredAt = time.Now().UTC()
	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("encode replayed job request: %w", err)
	}
	return bus.Publish(ctx, workflow.OutboxEvent{
		ID:      event.EventID,
		Subject: orchestration.JobRequestedSubject,
		Payload: payload,
	})
}

func environmentOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

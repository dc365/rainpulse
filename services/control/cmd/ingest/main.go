package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/bdpruntime"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/radaringest"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/runtimeconfig"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/jackc/pgx/v5/pgxpool"
	"gopkg.in/yaml.v3"
)

type radarConfiguration struct {
	ConfigVersion string `yaml:"config_version"`
	RadarID       string `yaml:"radar_id"`
	Lifecycle     string `yaml:"lifecycle"`
	DisplayName   string `yaml:"display_name"`
	Source        struct {
		Format string `yaml:"format"`
	} `yaml:"source"`
}

type sourceRuntime struct {
	source     radaringest.ManifestSource
	config     radarConfiguration
	configYAML []byte
	configJSON json.RawMessage
	scanner    *radaringest.Scanner
}

type sourceStatus struct {
	SourceID        string     `json:"source_id"`
	RadarID         string     `json:"radar_id"`
	ArrivalRoot     string     `json:"arrival_root"`
	LatestScanAt    *time.Time `json:"latest_scan_at,omitempty"`
	LatestSuccessAt *time.Time `json:"latest_success_at,omitempty"`
	LatestVolumeAt  *time.Time `json:"latest_volume_at,omitempty"`
	RegisteredCount uint64     `json:"registered_count"`
	FailureCount    uint64     `json:"failure_count"`
	LastError       string     `json:"last_error,omitempty"`
}

type statusBook struct {
	mu      sync.RWMutex
	profile string
	mode    string
	started time.Time
	sources map[string]sourceStatus
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	platformRuntime, err := bdpruntime.Prepare(bdpruntime.ComponentIngest, false)
	if err != nil {
		slog.Error("initialize Ruiyun BDP runtime for radar ingest", "error", err)
		os.Exit(1)
	}
	manifestPath := strings.TrimSpace(os.Getenv("RAINPULSE_RADAR_INGEST_MANIFEST"))
	if manifestPath == "" {
		slog.Error("RAINPULSE_RADAR_INGEST_MANIFEST is required")
		os.Exit(2)
	}
	manifest, err := radaringest.LoadManifest(manifestPath)
	if err != nil {
		slog.Error("load radar ingest manifest", "error", err)
		os.Exit(1)
	}
	if platformRuntime.PlatformAvailable {
		radarSource, sourceErr := bdpruntime.ResolveOriginalFileSource(
			platformRuntime.Config.RadarInput.DataCode,
			platformRuntime.Config.RadarInput.SourceIndex,
		)
		if sourceErr != nil {
			if platformRuntime.Required() {
				slog.Error("resolve radar ingest root from Ruiyun BDP metadata", "error", sourceErr)
				os.Exit(1)
			}
			slog.Warn("Ruiyun BDP radar metadata unavailable; retaining manifest arrival root", "error", sourceErr)
		} else {
			manifest, err = manifest.WithSourceSettings(
				radarSource.Root,
				platformRuntime.Config.RadarInput.ScanIntervalSeconds,
				platformRuntime.Config.RadarInput.MinimumFileAgeSeconds,
				platformRuntime.Config.RadarInput.LookbackHours,
			)
			if err != nil {
				slog.Error("apply Ruiyun BDP radar ingest root", "error", err)
				os.Exit(1)
			}
			slog.Info("Ruiyun BDP radar ingest root resolved", "data_code", radarSource.DataCode,
				"source_index", radarSource.SourceIndex, "root", radarSource.Root)
		}
	}
	pool, service, err := dependencies(ctx)
	if err != nil {
		slog.Error("initialize radar ingest", "error", err)
		os.Exit(1)
	}
	defer pool.Close()

	archive, err := radaringest.NewArchive(
		environmentOrDefault("RAINPULSE_OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
		os.Getenv("RAINPULSE_MINIO_WORKER_ACCESS_KEY"),
		os.Getenv("RAINPULSE_MINIO_WORKER_SECRET_KEY"),
		environmentOrDefault("RAINPULSE_OBJECT_STORE_BUCKET", "rainpulse"),
	)
	if err != nil {
		slog.Error("configure raw radar archive", "error", err)
		os.Exit(1)
	}

	runtimes, err := buildSourceRuntimes(manifest)
	if err != nil {
		slog.Error("configure radar ingest sources", "error", err)
		os.Exit(1)
	}
	book := newStatusBook(manifest, runtimes)
	server := startHealthServer(book)
	defer func() {
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()

	var group sync.WaitGroup
	for _, runtime := range runtimes {
		runtime := runtime
		group.Add(1)
		go func() {
			defer group.Done()
			runSource(ctx, manifest.Interval(), runtime, archive, service, book)
		}()
	}
	slog.Info("RainPulse multi-radar ingest ready",
		"profile", manifest.ProfileVersion,
		"mode", manifest.ExecutionMode,
		"source_count", len(runtimes),
	)
	<-ctx.Done()
	group.Wait()
}

func dependencies(
	ctx context.Context,
) (*pgxpool.Pool, *orchestration.Service, error) {
	databaseURL, err := runtimeconfig.DatabaseURL()
	if err != nil {
		return nil, nil, err
	}
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, nil, fmt.Errorf("open PostgreSQL pool: %w", err)
	}
	startup, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := pool.Ping(startup); err != nil {
		pool.Close()
		return nil, nil, fmt.Errorf("ping PostgreSQL: %w", err)
	}
	store := postgresstore.New(pool)
	return pool, orchestration.NewService(store, orchestration.Options{}), nil
}

func buildSourceRuntimes(manifest radaringest.Manifest) ([]sourceRuntime, error) {
	runtimes := make([]sourceRuntime, 0, len(manifest.Sources))
	for _, source := range manifest.Sources {
		payload, err := os.ReadFile(source.ConfigPath)
		if err != nil {
			return nil, fmt.Errorf("read %s radar config: %w", source.SourceID, err)
		}
		var config radarConfiguration
		if err := yaml.Unmarshal(payload, &config); err != nil {
			return nil, fmt.Errorf("decode %s radar config: %w", source.SourceID, err)
		}
		config.RadarID = strings.ToLower(strings.TrimSpace(config.RadarID))
		if config.RadarID != source.RadarID {
			return nil, fmt.Errorf(
				"ingest source %s expects radar %s but config declares %s",
				source.SourceID, source.RadarID, config.RadarID,
			)
		}
		if manifest.ExecutionMode == "operational" && config.Lifecycle != "ready" {
			return nil, fmt.Errorf("operational ingest requires ready radar config: %s", source.RadarID)
		}
		var normalized map[string]any
		if err := yaml.Unmarshal(payload, &normalized); err != nil {
			return nil, fmt.Errorf("normalize %s radar config: %w", source.SourceID, err)
		}
		configJSON, err := json.Marshal(normalized)
		if err != nil {
			return nil, fmt.Errorf("encode %s radar config: %w", source.SourceID, err)
		}
		scanner, err := radaringest.NewPersistentScannerWithFilter(
			source.ArrivalRoot,
			source.MinimumAge(),
			source.Lookback(),
			manifest.StatePath(source),
			source.FileNameContains,
		)
		if err != nil {
			return nil, fmt.Errorf("configure %s scanner: %w", source.SourceID, err)
		}
		runtimes = append(runtimes, sourceRuntime{
			source: source, config: config, configYAML: payload,
			configJSON: configJSON, scanner: scanner,
		})
	}
	return runtimes, nil
}

func runSource(
	ctx context.Context,
	interval time.Duration,
	runtime sourceRuntime,
	archive *radaringest.Archive,
	service *orchestration.Service,
	book *statusBook,
) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		scanSource(ctx, runtime, archive, service, book)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func scanSource(
	ctx context.Context,
	runtime sourceRuntime,
	archive *radaringest.Archive,
	service *orchestration.Service,
	book *statusBook,
) {
	now := time.Now().UTC()
	book.scanned(runtime.source.SourceID, now)
	paths, err := runtime.scanner.Scan(now)
	if err != nil {
		book.failed(runtime.source.SourceID, err)
		slog.Error("scan radar source", "source_id", runtime.source.SourceID,
			"radar_id", runtime.source.RadarID, "error", err)
		return
	}
	for _, path := range paths {
		volumeStart, err := ingestFile(ctx, runtime, path, archive, service)
		if err != nil {
			book.failed(runtime.source.SourceID, err)
			slog.Error("ingest radar file", "source_id", runtime.source.SourceID,
				"radar_id", runtime.source.RadarID, "path", path, "error", err)
			continue
		}
		if err := runtime.scanner.MarkProcessed(path); err != nil {
			book.failed(runtime.source.SourceID, err)
			slog.Error("persist processed radar file", "source_id", runtime.source.SourceID,
				"path", path, "error", err)
			continue
		}
		book.succeeded(runtime.source.SourceID, volumeStart)
		slog.Info("radar arrival archived and registered",
			"source_id", runtime.source.SourceID,
			"radar_id", runtime.source.RadarID,
			"path", path,
			"volume_start", volumeStart,
		)
	}
}

func ingestFile(
	ctx context.Context,
	runtime sourceRuntime,
	path string,
	archive *radaringest.Archive,
	service *orchestration.Service,
) (time.Time, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return time.Time{}, fmt.Errorf("resolve radar input path: %w", err)
	}
	if !strings.Contains(strings.ToLower(filepath.Base(absolute)), runtime.source.RadarID) {
		return time.Time{}, fmt.Errorf("radar filename does not contain expected station %s", runtime.source.RadarID)
	}
	info, err := os.Stat(absolute)
	if err != nil || !info.Mode().IsRegular() {
		return time.Time{}, fmt.Errorf("radar input is not a readable regular file")
	}
	start, end, err := radaringest.ProbeVolumeTimes(absolute)
	if err != nil {
		return time.Time{}, err
	}
	inputHash, err := sha256File(absolute)
	if err != nil {
		return time.Time{}, err
	}
	stable, err := os.Stat(absolute)
	if err != nil {
		return time.Time{}, fmt.Errorf("restat radar input after inspection: %w", err)
	}
	if stable.Size() != info.Size() || !stable.ModTime().Equal(info.ModTime()) {
		return time.Time{}, fmt.Errorf("radar input changed while it was being inspected")
	}
	inputURI, err := archive.File(ctx, runtime.config.RadarID, start, inputHash, absolute)
	if err != nil {
		return time.Time{}, err
	}
	configHash := sha256.Sum256(runtime.configYAML)
	displayName := runtime.config.DisplayName
	_, _, err = service.CreateRadarDecode(ctx, orchestration.RadarDecodeInput{
		RadarID: runtime.config.RadarID, DisplayName: &displayName,
		Lifecycle:     workflow.RadarLifecycle(runtime.config.Lifecycle),
		ConfigVersion: runtime.config.ConfigVersion, Config: runtime.configJSON,
		ConfigSHA256: fmt.Sprintf("%x", configHash), SourceFormat: runtime.config.Source.Format,
		InputURI: inputURI, InputSHA256: inputHash, InputSizeBytes: info.Size(),
		VolumeStartTime: start, VolumeEndTime: end,
	})
	if err != nil {
		return time.Time{}, err
	}
	return start.UTC(), nil
}

func sha256File(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", fmt.Errorf("open radar input: %w", err)
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", fmt.Errorf("hash radar input: %w", err)
	}
	return fmt.Sprintf("%x", digest.Sum(nil)), nil
}

func newStatusBook(manifest radaringest.Manifest, runtimes []sourceRuntime) *statusBook {
	book := &statusBook{
		profile: manifest.ProfileVersion,
		mode:    manifest.ExecutionMode,
		started: time.Now().UTC(),
		sources: make(map[string]sourceStatus, len(runtimes)),
	}
	for _, runtime := range runtimes {
		book.sources[runtime.source.SourceID] = sourceStatus{
			SourceID:    runtime.source.SourceID,
			RadarID:     runtime.source.RadarID,
			ArrivalRoot: runtime.source.ArrivalRoot,
		}
	}
	return book
}

func (book *statusBook) scanned(sourceID string, at time.Time) {
	book.mu.Lock()
	defer book.mu.Unlock()
	status := book.sources[sourceID]
	value := at.UTC()
	status.LatestScanAt = &value
	book.sources[sourceID] = status
}

func (book *statusBook) succeeded(sourceID string, volumeTime time.Time) {
	book.mu.Lock()
	defer book.mu.Unlock()
	status := book.sources[sourceID]
	succeededAt := time.Now().UTC()
	volumeAt := volumeTime.UTC()
	status.LatestSuccessAt = &succeededAt
	status.LatestVolumeAt = &volumeAt
	status.RegisteredCount++
	status.LastError = ""
	book.sources[sourceID] = status
}

func (book *statusBook) failed(sourceID string, err error) {
	book.mu.Lock()
	defer book.mu.Unlock()
	status := book.sources[sourceID]
	status.FailureCount++
	status.LastError = err.Error()
	book.sources[sourceID] = status
}

func (book *statusBook) snapshot() map[string]any {
	book.mu.RLock()
	defer book.mu.RUnlock()
	sources := make([]sourceStatus, 0, len(book.sources))
	for _, status := range book.sources {
		sources = append(sources, status)
	}
	sort.Slice(sources, func(left, right int) bool {
		return sources[left].RadarID < sources[right].RadarID
	})
	return map[string]any{
		"status":          "ready",
		"profile_version": book.profile,
		"execution_mode":  book.mode,
		"started_at":      book.started,
		"sources":         sources,
	}
}

func startHealthServer(book *statusBook) *http.Server {
	server := &http.Server{
		Addr:              environmentOrDefault("RAINPULSE_INGEST_HEALTH_ADDR", ":8092"),
		ReadHeaderTimeout: 3 * time.Second,
		Handler: http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
			if request.URL.Path != "/healthz" && request.URL.Path != "/status" {
				http.NotFound(response, request)
				return
			}
			response.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(response).Encode(book.snapshot())
		}),
	}
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("radar ingest health server stopped", "error", err)
		}
	}()
	return server
}

func environmentOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

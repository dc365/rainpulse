package ingestapp

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/radaringest"
)

// HistoryMain is a one-shot UTC import path; it never changes the live scanner
// ledger. Preview is the default and never opens a database or object store.
func HistoryMain() {
	manifestPath := flag.String("manifest", os.Getenv("RAINPULSE_RADAR_INGEST_MANIFEST"), "ingest manifest path")
	sourceID := flag.String("source", "", "manifest source_id")
	startText := flag.String("start", "", "inclusive UTC RFC3339 time")
	endText := flag.String("end", "", "exclusive UTC RFC3339 time")
	execute := flag.Bool("execute", false, "archive and register the previewed files")
	flag.Parse()
	if err := runHistoricalImport(context.Background(), *manifestPath, *sourceID, *startText, *endText, *execute); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func runHistoricalImport(ctx context.Context, manifestPath, sourceID, startText, endText string, execute bool) error {
	start, err := time.Parse(time.RFC3339, startText)
	if err != nil {
		return fmt.Errorf("parse historical start UTC: %w", err)
	}
	end, err := time.Parse(time.RFC3339, endText)
	if err != nil {
		return fmt.Errorf("parse historical end UTC: %w", err)
	}
	if start.Location() != time.UTC || end.Location() != time.UTC || !start.Before(end) {
		return fmt.Errorf("historical window must be increasing and use Z UTC timestamps")
	}
	if start.Format("20060102") != end.Add(-time.Nanosecond).Format("20060102") {
		return fmt.Errorf("historical import window must stay within one UTC date")
	}
	if execute && end.Sub(start) > time.Hour {
		return fmt.Errorf("execute window cannot exceed one hour")
	}
	manifest, err := radaringest.LoadManifest(manifestPath)
	if err != nil {
		return err
	}
	var source *radaringest.ManifestSource
	for index := range manifest.Sources {
		if manifest.Sources[index].SourceID == strings.ToLower(sourceID) {
			source = &manifest.Sources[index]
			break
		}
	}
	if source == nil {
		return fmt.Errorf("historical source_id %q is absent from manifest", sourceID)
	}
	day := time.Date(start.Year(), start.Month(), start.Day(), 0, 0, 0, 0, time.UTC)
	files, err := radaringest.DiscoverHistoricalFiles(source.ArrivalRoot, source.RadarID, day, start, end)
	if err != nil {
		return err
	}
	if execute && len(files) > 64 {
		return fmt.Errorf("execute window contains %d files; limit is 64", len(files))
	}
	encoder := json.NewEncoder(os.Stdout)
	for _, file := range files {
		if err := encoder.Encode(file); err != nil {
			return err
		}
	}
	if !execute {
		return nil
	}
	manifest.Sources = []radaringest.ManifestSource{*source}
	runtimes, err := buildSourceRuntimes(manifest)
	if err != nil {
		return err
	}
	archive, err := radaringest.NewArchive(
		environmentOrDefault("RAINPULSE_OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
		os.Getenv("RAINPULSE_MINIO_WORKER_ACCESS_KEY"),
		os.Getenv("RAINPULSE_MINIO_WORKER_SECRET_KEY"),
		environmentOrDefault("RAINPULSE_OBJECT_STORE_BUCKET", "rainpulse"),
	)
	if err != nil {
		return err
	}
	pool, service, err := dependencies(ctx)
	if err != nil {
		return err
	}
	defer pool.Close()
	for _, file := range files {
		if _, err := ingestFile(ctx, runtimes[0], file.Path, archive, service); err != nil {
			return fmt.Errorf("register %s: %w", file.Path, err)
		}
	}
	return encoder.Encode(map[string]any{"registered_count": len(files), "source_id": source.SourceID})
}

package ingestapp

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/radaringest"
	"github.com/jackc/pgx/v5/pgconn"
)

// HistoryMain is a one-shot UTC import path; it never changes the live scanner
// ledger. Preview is the default and never opens a database or object store.
func HistoryMain() {
	manifestPath := flag.String("manifest", os.Getenv("RAINPULSE_RADAR_INGEST_MANIFEST"), "ingest manifest path")
	sourceID := flag.String("source", "", "manifest source_id")
	startText := flag.String("start", "", "inclusive UTC RFC3339 time")
	endText := flag.String("end", "", "exclusive UTC RFC3339 time")
	cadence := flag.Int("cadence-seconds", 0, "0 retains all; 360 selects first native volume per six-minute UTC bucket")
	skipInvalid := flag.Bool("skip-invalid", false, "report invalid files individually and continue")
	execute := flag.Bool("execute", false, "archive and register the previewed files")
	flag.Parse()
	if err := runHistoricalImport(context.Background(), *manifestPath, *sourceID, *startText, *endText, *execute, historyOptions{CadenceSeconds: *cadence, SkipInvalid: *skipInvalid}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

type historyOptions struct {
	CadenceSeconds int
	SkipInvalid    bool
}

func runHistoricalImport(ctx context.Context, manifestPath, sourceID, startText, endText string, execute bool, options ...historyOptions) error {
	var opts historyOptions
	if len(options) > 1 {
		return fmt.Errorf("one history options object required")
	}
	if len(options) == 1 {
		opts = options[0]
	}
	if opts.CadenceSeconds != 0 && opts.CadenceSeconds != 360 {
		return fmt.Errorf("cadence-seconds must be 0 or 360")
	}
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
	var files []radaringest.HistoricalFile
	var rejected []radaringest.HistoricalIssue
	if opts.SkipInvalid {
		files, rejected, err = radaringest.DiscoverHistoricalFilesReport(source.ArrivalRoot, source.RadarID, day, start, end)
	} else {
		files, err = radaringest.DiscoverHistoricalFiles(source.ArrivalRoot, source.RadarID, day, start, end)
	}
	if err != nil {
		return err
	}
	var omitted []radaringest.HistoricalFile
	if opts.CadenceSeconds != 0 {
		files, omitted = radaringest.SelectHistoricalCadence(files, time.Duration(opts.CadenceSeconds)*time.Second)
	}
	if execute && len(files) > 64 {
		return fmt.Errorf("execute window contains %d files; limit is 64", len(files))
	}
	encoder := json.NewEncoder(os.Stdout)
	if opts.SkipInvalid || opts.CadenceSeconds != 0 {
		if err := encoder.Encode(map[string]any{"inventory_report": true, "rejected": rejected, "cadence_omitted": omitted, "selected_count": len(files)}); err != nil {
			return err
		}
	}
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
	registered := 0
	existing := 0
	for _, file := range files {
		var alreadyRegistered bool
		if err := pool.QueryRow(ctx, `SELECT EXISTS (
			SELECT 1 FROM radar_scans WHERE radar_id=$1 AND volume_start_time=$2 AND volume_end_time=$3
		)`, file.RadarID, file.Start, file.End).Scan(&alreadyRegistered); err != nil {
			return fmt.Errorf("check existing historical scan %s: %w", file.Path, err)
		}
		if alreadyRegistered {
			existing++
			continue
		}
		if _, err := ingestFile(ctx, runtimes[0], file.Path, archive, service); err != nil {
			var databaseError *pgconn.PgError
			if errors.As(err, &databaseError) && databaseError.Code == "23505" &&
				databaseError.ConstraintName == "radar_scan_runs_scan_id_key" {
				existing++
				continue
			}
			return fmt.Errorf("register %s: %w", file.Path, err)
		}
		registered++
	}
	return encoder.Encode(map[string]any{
		"registered_count": registered, "existing_count": existing, "source_id": source.SourceID,
	})
}

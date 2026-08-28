package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
)

func TestRequestedSubjectCoversEveryRequestEvent(t *testing.T) {
	tests := []struct {
		eventType string
		subject   string
	}{
		{orchestration.JobRequestedEventType, orchestration.JobRequestedSubject},
		{orchestration.RadarDecodeRequestedEventType, orchestration.RadarDecodeRequestedSubject},
		{orchestration.RadarQCRequestedEventType, orchestration.RadarQCRequestedSubject},
		{orchestration.RadarGridRequestedEventType, orchestration.RadarGridRequestedSubject},
		{orchestration.AnalysisMosaicRequestedEventType, orchestration.AnalysisMosaicRequestedSubject},
		{orchestration.AnalysisQPERequestedEventType, orchestration.AnalysisQPERequestedSubject},
		{orchestration.AnalysisDiagnosticsRequestedEventType, orchestration.AnalysisDiagnosticsRequestedSubject},
		{orchestration.NowcastInputRequestedEventType, orchestration.NowcastInputRequestedSubject},
		{orchestration.PystepsLKRequestedEventType, orchestration.PystepsLKRequestedSubject},
		{orchestration.ProductBuildRequestedEventType, orchestration.ProductBuildRequestedSubject},
	}
	for _, test := range tests {
		t.Run(test.eventType, func(t *testing.T) {
			got, err := requestedSubject(test.eventType)
			if err != nil || got != test.subject {
				t.Fatalf("requestedSubject(%q) = %q, want %q", test.eventType, got, test.subject)
			}
		})
	}
	if _, err := requestedSubject("unknown.requested.v1"); err == nil {
		t.Fatal("unknown replay event type was routed instead of rejected")
	}
}

func TestRadarIngestSettingsAreDisabledByDefault(t *testing.T) {
	t.Setenv("RAINPULSE_RADAR_INGEST_ENABLED", "false")
	settings, err := radarIngestSettingsFromEnvironment()
	if err != nil || settings != nil {
		t.Fatalf("disabled settings = %#v, err=%v", settings, err)
	}
}

func TestRadarIngestSettingsValidateConfiguredWatcher(t *testing.T) {
	root := t.TempDir()
	config := filepath.Join(root, "radar.yaml")
	if err := os.WriteFile(config, []byte("radar_id: z9598\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("RAINPULSE_RADAR_INGEST_ENABLED", "true")
	t.Setenv("RAINPULSE_RADAR_INGEST_CONFIG", config)
	t.Setenv("RAINPULSE_RADAR_INGEST_ROOT", root)
	t.Setenv("RAINPULSE_RADAR_INGEST_INTERVAL", "20s")
	t.Setenv("RAINPULSE_RADAR_INGEST_MIN_AGE", "45s")
	t.Setenv("RAINPULSE_RADAR_INGEST_LOOKBACK", "12h")

	settings, err := radarIngestSettingsFromEnvironment()
	if err != nil {
		t.Fatal(err)
	}
	if settings == nil || settings.interval != 20*time.Second || settings.minAge != 45*time.Second || settings.lookback != 12*time.Hour {
		t.Fatalf("unexpected settings: %#v", settings)
	}
}

package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
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
		{orchestration.NowcastNetShadowRequestedEventType, orchestration.NowcastNetShadowRequestedSubject},
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

func TestDiscoverRadarBatchInputsSelectsOnlyRegularCAPFMTVolumes(t *testing.T) {
	root := t.TempDir()
	configs := filepath.Join(root, "configs")
	inputs := filepath.Join(root, "inputs")
	for _, radarID := range []string{"z9591", "z9593"} {
		if err := os.MkdirAll(filepath.Join(inputs, strings.ToUpper(radarID)), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(configs, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(
			filepath.Join(configs, radarID+".yaml"),
			[]byte("radar_id: "+radarID+"\n"),
			0o600,
		); err != nil {
			t.Fatal(err)
		}
		for _, name := range []string{
			"Z_RADR_I_" + strings.ToUpper(radarID) + "_20260828095000_O_DOR_SAD_CAP_FMT.bin.bz2",
			"Z_RADR_I_" + strings.ToUpper(radarID) + "_20260828094500_O_DOR_SAD_CAP_FMT.bin.bz2",
			"Z_RADR_I_" + strings.ToUpper(radarID) + "_20260828095000_O_DOR_SAD_CAP_FMT_DPCTEST.bin.bz2",
		} {
			if err := os.WriteFile(filepath.Join(inputs, strings.ToUpper(radarID), name), []byte("test"), 0o600); err != nil {
				t.Fatal(err)
			}
		}
	}

	discovered, err := discoverRadarBatchInputs(configs, inputs)
	if err != nil {
		t.Fatal(err)
	}
	if len(discovered) != 4 {
		t.Fatalf("unexpected radar batch inputs: %#v", discovered)
	}
	wantOrder := []struct {
		radarID string
		timeKey string
	}{
		{"z9591", "20260828094500"},
		{"z9593", "20260828094500"},
		{"z9591", "20260828095000"},
		{"z9593", "20260828095000"},
	}
	for index, want := range wantOrder {
		if discovered[index].radarID != want.radarID || radarBatchChronologyKey(discovered[index].inputPath) != want.timeKey {
			t.Fatalf("batch input %d = %#v, want radar=%s time=%s", index, discovered[index], want.radarID, want.timeKey)
		}
	}
	for _, input := range discovered {
		if strings.Contains(input.inputPath, "DPCTEST") {
			t.Fatalf("DPCTEST input was not excluded: %s", input.inputPath)
		}
	}
}

func TestRadarQCContextSelectsNearbyTimeAndOneCrossRadarVolume(t *testing.T) {
	issueTime := time.Date(2026, 8, 28, 2, 30, 0, 0, time.UTC)
	uri := func(value string) *string { return &value }
	target := workflow.RadarScan{
		ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime,
	}
	candidates := []workflow.RadarScan{
		{ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime.Add(-5 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9591/02500")},
		{ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime.Add(-10 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9591/02450")},
		{ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime.Add(-15 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9591/02445")},
		{ID: uuid.New(), RadarID: "z9593", VolumeEndTime: issueTime.Add(-20 * time.Second), NormalizedURI: uri("s3://rainpulse/z9593/02500")},
		{ID: uuid.New(), RadarID: "z9593", VolumeEndTime: issueTime.Add(-2 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9593/02480")},
		{ID: uuid.New(), RadarID: "z9598", VolumeEndTime: issueTime.Add(4 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9598/02540")},
		{ID: uuid.New(), RadarID: "z9599", VolumeEndTime: issueTime.Add(6 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9599/02560")},
	}

	temporal, crossRadar := radarQCContextFromScans(target, candidates, qcContextFusionConfiguration{
		Enabled: true, MaximumTemporalContextScans: 2, CrossRadarMaximumTimeOffsetSecs: 300,
	})

	if len(temporal) != 2 || temporal[0].InputURI != "s3://rainpulse/z9591/02500" ||
		temporal[1].InputURI != "s3://rainpulse/z9591/02450" {
		t.Fatalf("unexpected temporal context: %#v", temporal)
	}
	if len(crossRadar) != 2 || crossRadar[0].RadarID != "z9593" ||
		crossRadar[0].InputURI != "s3://rainpulse/z9593/02500" || crossRadar[1].RadarID != "z9598" {
		t.Fatalf("unexpected cross-radar context: %#v", crossRadar)
	}
}

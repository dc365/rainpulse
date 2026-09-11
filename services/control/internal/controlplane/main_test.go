package controlplane

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

type fakeRadarQCContextStore struct {
	candidates          []workflow.RadarScan
	err                 error
	called              bool
	scanID              uuid.UUID
	radarID             string
	volumeEndTime       time.Time
	sameRadarWindow     time.Duration
	crossRadarWindow    time.Duration
	allowFutureTemporal bool
}

func (store *fakeRadarQCContextStore) ListRadarQCContextCandidates(
	_ context.Context,
	scanID uuid.UUID,
	radarID string,
	volumeEndTime time.Time,
	sameRadarWindow time.Duration,
	crossRadarWindow time.Duration,
	allowFutureTemporal bool,
) ([]workflow.RadarScan, error) {
	store.called = true
	store.scanID = scanID
	store.radarID = radarID
	store.volumeEndTime = volumeEndTime
	store.sameRadarWindow = sameRadarWindow
	store.crossRadarWindow = crossRadarWindow
	store.allowFutureTemporal = allowFutureTemporal
	return store.candidates, store.err
}

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
			got, err := requestedSubject(test.eventType, orchestration.PystepsLKModelVersion)
			if err != nil || got != test.subject {
				t.Fatalf("requestedSubject(%q) = %q, want %q", test.eventType, got, test.subject)
			}
		})
	}
	if _, err := requestedSubject("unknown.requested.v1", ""); err == nil {
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
	if len(crossRadar) != 1 || crossRadar[0].RadarID != "z9593" ||
		crossRadar[0].InputURI != "s3://rainpulse/z9593/02500" {
		t.Fatalf("unexpected cross-radar context: %#v", crossRadar)
	}
}

func TestRadarQCContextRejectsFutureSameRadarAndDuplicateURI(t *testing.T) {
	issueTime := time.Date(2026, 8, 28, 2, 30, 0, 0, time.UTC)
	uri := func(value string) *string { return &value }
	target := workflow.RadarScan{
		ID: uuid.New(), RadarID: "Z9591", VolumeEndTime: issueTime,
	}
	candidates := []workflow.RadarScan{
		{ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime.Add(2 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9591/future")},
		{ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime.Add(-(15*time.Minute + time.Second)), NormalizedURI: uri("s3://rainpulse/z9591/reject-901")},
		{ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime.Add(-15 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9591/accept-900")},
		{ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime.Add(-10 * time.Minute), NormalizedURI: uri("s3://rainpulse/shared")},
		{ID: uuid.New(), RadarID: "Z9591", VolumeEndTime: issueTime.Add(-5 * time.Minute), NormalizedURI: uri("s3://rainpulse/shared")},
	}

	temporal, crossRadar := radarQCContextFromScans(target, candidates, qcContextFusionConfiguration{
		Enabled: true, MaximumTemporalContextScans: 3, TemporalMaxTimeOffsetSecs: 900,
	})

	if len(crossRadar) != 0 {
		t.Fatalf("unexpected cross-radar context: %#v", crossRadar)
	}
	if len(temporal) != 2 {
		t.Fatalf("unexpected temporal context size: %#v", temporal)
	}
	if temporal[0].InputURI != "s3://rainpulse/shared" || temporal[1].InputURI != "s3://rainpulse/z9591/accept-900" {
		t.Fatalf("unexpected temporal selection: %#v", temporal)
	}
}

func TestRadarQCContextSymmetricOfflineOrdersPastBeforeFutureOnTie(t *testing.T) {
	issueTime := time.Date(2026, 8, 28, 2, 30, 0, 0, time.UTC)
	uri := func(value string) *string { return &value }
	pastID := uuid.MustParse("10000000-0000-4000-8000-0000000000ff")
	futureID := uuid.MustParse("10000000-0000-4000-8000-000000000001")
	target := workflow.RadarScan{ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime}
	candidates := []workflow.RadarScan{
		{ID: futureID, RadarID: "z9591", VolumeEndTime: issueTime.Add(5 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9591/future")},
		{ID: pastID, RadarID: "z9591", VolumeEndTime: issueTime.Add(-5 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9591/past")},
	}

	temporal, crossRadar := radarQCContextFromScans(target, candidates, qcContextFusionConfiguration{
		Enabled: true, MaximumTemporalContextScans: 2, TemporalMaxTimeOffsetSecs: 900,
		TemporalSelectionMode: "symmetric_offline",
	})

	if len(crossRadar) != 0 {
		t.Fatalf("unexpected cross-radar context: %#v", crossRadar)
	}
	if len(temporal) != 2 || temporal[0].InputURI != "s3://rainpulse/z9591/past" || temporal[1].InputURI != "s3://rainpulse/z9591/future" {
		t.Fatalf("unexpected symmetric temporal ordering: %#v", temporal)
	}
}

func TestSelectRadarQCContextUsesWindowedStoreQuery(t *testing.T) {
	issueTime := time.Date(2026, 8, 28, 2, 30, 0, 0, time.UTC)
	uri := func(value string) *string { return &value }
	target := workflow.RadarScan{
		ID:      uuid.MustParse("10000000-0000-4000-8000-000000000100"),
		RadarID: "z9591", VolumeEndTime: issueTime,
	}
	store := &fakeRadarQCContextStore{candidates: []workflow.RadarScan{
		{ID: uuid.New(), RadarID: "z9591", VolumeEndTime: issueTime.Add(-5 * time.Minute), NormalizedURI: uri("s3://rainpulse/z9591/02500")},
		{ID: uuid.New(), RadarID: "z9593", VolumeEndTime: issueTime.Add(-20 * time.Second), NormalizedURI: uri("s3://rainpulse/z9593/02500")},
	}}

	temporal, crossRadar, err := selectRadarQCContext(context.Background(), store, target, qcContextFusionConfiguration{
		Enabled: true, MaximumTemporalContextScans: 1,
	})
	if err != nil {
		t.Fatalf("selectRadarQCContext error = %v", err)
	}
	if !store.called {
		t.Fatal("expected windowed QC context store query")
	}
	if store.scanID != target.ID || store.radarID != target.RadarID || !store.volumeEndTime.Equal(issueTime) {
		t.Fatalf("unexpected query identity: %#v", store)
	}
	if store.sameRadarWindow != 15*time.Minute || store.crossRadarWindow != 5*time.Minute || store.allowFutureTemporal {
		t.Fatalf("unexpected query windows: %#v", store)
	}
	if len(temporal) != 1 || temporal[0].InputURI != "s3://rainpulse/z9591/02500" {
		t.Fatalf("unexpected temporal context: %#v", temporal)
	}
	if len(crossRadar) != 1 || crossRadar[0].InputURI != "s3://rainpulse/z9593/02500" {
		t.Fatalf("unexpected cross-radar context: %#v", crossRadar)
	}
}

func TestRadarQCContextPastOnlyRejectsFutureNeighbourAndSameInstant(t *testing.T) {
	now := time.Date(2026, 9, 8, 0, 0, 0, 0, time.UTC)
	uri := func(s string) *string { return &s }
	target := workflow.RadarScan{ID: uuid.New(), RadarID: "z9598", VolumeEndTime: now}
	inputs := []workflow.RadarScan{
		{ID: uuid.New(), RadarID: "z9598", VolumeEndTime: now, NormalizedURI: uri("s3://rainpulse/equal")},
		{ID: uuid.New(), RadarID: "z9599", VolumeEndTime: now.Add(300 * time.Second), NormalizedURI: uri("s3://rainpulse/future")},
		{ID: uuid.New(), RadarID: "z9599", VolumeEndTime: now.Add(-300 * time.Second), NormalizedURI: uri("s3://rainpulse/past")},
	}
	temporal, cross := radarQCContextFromScans(target, inputs, qcContextFusionConfiguration{Enabled: true, TemporalSelectionMode: "past_only"})
	if len(temporal) != 0 || len(cross) != 1 || cross[0].InputURI != "s3://rainpulse/past" {
		t.Fatalf("causal contexts: temporal=%v cross=%v", temporal, cross)
	}
}

func TestRequestedSubjectKeepsLegacyLKReplayOffV2Queue(t *testing.T) {
	subject, err := requestedSubject(orchestration.PystepsLKRequestedEventType, "pysteps-lk-1.1.0")
	if err != nil || subject != "rainpulse.jobs.requested.pysteps_lk" {
		t.Fatalf("legacy subject = %q, error = %v", subject, err)
	}
	if _, err := requestedSubject(orchestration.PystepsLKRequestedEventType, "unknown"); err == nil {
		t.Fatal("unknown LK version routed to active queue")
	}
}

package api_test

import (
	"bufio"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/api"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

func TestSystemStatusReportsReadyControlPlane(t *testing.T) {
	handler := api.NewHandler(api.Options{Version: "test-version"})
	request := httptest.NewRequest(http.MethodGet, "/api/v1/system/status", nil)
	response := httptest.NewRecorder()

	handler.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", response.Code)
	}
	if got := response.Header().Get("Content-Type"); got != "application/json" {
		t.Fatalf("expected application/json content type, got %q", got)
	}

	var body struct {
		Service string `json:"service"`
		Status  string `json:"status"`
		Version string `json:"version"`
	}
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		t.Fatalf("decode status response: %v", err)
	}

	if body.Service != "rainpulse-control" {
		t.Fatalf("expected rainpulse-control service, got %q", body.Service)
	}
	if body.Status != "ready" {
		t.Fatalf("expected ready status, got %q", body.Status)
	}
	if body.Version != "test-version" {
		t.Fatalf("expected injected version, got %q", body.Version)
	}
}

func TestRunQueriesAndRerunUseControlPlane(t *testing.T) {
	now := time.Date(2026, 8, 24, 3, 0, 0, 0, time.UTC)
	run := workflow.Run{
		ID:            uuid.MustParse("f3641335-13a3-4f68-96c0-56a5e0e684d7"),
		IssueTime:     now,
		GridID:        "rp003-sim-grid",
		ConfigVersion: "rp003-sim-v1",
		Status:        workflow.RunBaselineRunning,
		CreatedAt:     now,
		UpdatedAt:     now,
	}
	job := workflow.Job{
		ID:            uuid.MustParse("0894481f-c096-49af-8d32-e9c531a66772"),
		RunID:         run.ID,
		JobType:       "model.pysteps_lk",
		ModelVersion:  "pysteps-lk-sim-v1",
		ConfigVersion: "rp003-sim-v1",
		Status:        workflow.JobRunning,
		Attempt:       1,
		CreatedAt:     now,
	}
	store := &fakeRunStore{run: run, jobs: []workflow.Job{job}}
	commands := &fakeRunCommands{run: workflow.Run{
		ID:            uuid.MustParse("47413539-2d55-41f7-8c22-788113dbeced"),
		IssueTime:     now,
		GridID:        run.GridID,
		ConfigVersion: run.ConfigVersion,
		Status:        workflow.RunBaselineRunning,
		CreatedAt:     now.Add(time.Minute),
		UpdatedAt:     now.Add(time.Minute),
	}}
	handler := api.NewHandler(api.Options{Version: "test", Runs: store, Commands: commands})

	assertStatus := func(method, target string, want int) *httptest.ResponseRecorder {
		t.Helper()
		request := httptest.NewRequest(method, target, nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != want {
			t.Fatalf("%s %s: got status %d, want %d; body=%s", method, target, response.Code, want, response.Body.String())
		}
		return response
	}

	latest := assertStatus(http.MethodGet, "/api/v1/runs/latest", http.StatusOK)
	if !strings.Contains(latest.Body.String(), `"status":"BASELINE_RUNNING"`) {
		t.Fatalf("latest run response has wrong state: %s", latest.Body.String())
	}
	jobs := assertStatus(http.MethodGet, "/api/v1/runs/"+run.ID.String()+"/jobs", http.StatusOK)
	if !strings.Contains(jobs.Body.String(), `"status":"RUNNING"`) {
		t.Fatalf("jobs response has wrong state: %s", jobs.Body.String())
	}
	rerun := assertStatus(http.MethodPost, "/api/v1/admin/runs/"+run.ID.String()+"/rerun", http.StatusAccepted)
	if !strings.Contains(rerun.Body.String(), commands.run.ID.String()) || commands.source != run.ID {
		t.Fatalf("rerun was not delegated correctly: %s", rerun.Body.String())
	}
}

func TestRunEventStreamSendsCurrentState(t *testing.T) {
	now := time.Date(2026, 8, 24, 3, 0, 0, 0, time.UTC)
	run := workflow.Run{
		ID:        uuid.MustParse("f3641335-13a3-4f68-96c0-56a5e0e684d7"),
		IssueTime: now, GridID: "grid", ConfigVersion: "config",
		Status: workflow.RunBaselineReady, CreatedAt: now, UpdatedAt: now,
	}
	handler := api.NewHandler(api.Options{
		Version: "test", Runs: &fakeRunStore{run: run}, SSEPollInterval: 5 * time.Millisecond,
	})
	server := httptest.NewServer(handler)
	defer server.Close()

	response, err := http.Get(server.URL + "/api/v1/events/stream?run_id=" + run.ID.String()) //nolint:noctx
	if err != nil {
		t.Fatalf("open SSE stream: %v", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || response.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("unexpected SSE response: status=%d content-type=%q", response.StatusCode, response.Header.Get("Content-Type"))
	}
	reader := bufio.NewReader(response.Body)
	var event strings.Builder
	for range 4 {
		line, err := reader.ReadString('\n')
		if err != nil {
			t.Fatalf("read SSE event: %v", err)
		}
		event.WriteString(line)
	}
	if !strings.Contains(event.String(), "event: run.updated") || !strings.Contains(event.String(), `"status":"BASELINE_READY"`) {
		t.Fatalf("unexpected SSE event: %s", event.String())
	}
}

func TestRadarAndAnalysisQueriesPreservePartialRadarFailure(t *testing.T) {
	now := time.Date(2026, 8, 24, 3, 0, 0, 0, time.UTC)
	scanAID := uuid.MustParse("10000000-0000-4000-8000-000000000001")
	scanBID := uuid.MustParse("10000000-0000-4000-8000-000000000002")
	reason := "synthetic radar B failed; analysis continued with radar A"
	failedReason := "SIMULATED_RADAR_FAILURE"
	quality := 0.82
	complete := 1.0
	analysis := workflow.AnalysisCycle{
		ID:           uuid.MustParse("20000000-0000-4000-8000-000000000001"),
		RunID:        uuid.MustParse("20000000-0000-4000-8000-000000000002"),
		AnalysisTime: now, GridID: "synthetic-grid", ConfigVersion: "analysis-v1",
		Status: workflow.AnalysisReady, DegradedReason: &reason, RadarCount: 1,
		ValidCoverageRatio: floatPointer(0.86), MeanQualityIndex: &quality,
		Radars: []workflow.AnalysisRadar{
			{RadarID: "synthetic_radar_a", ScanID: &scanAID, State: workflow.AnalysisRadarParticipating},
			{RadarID: "synthetic_radar_b", ScanID: &scanBID, State: workflow.AnalysisRadarFailed, ExclusionReason: &failedReason},
		},
		CreatedAt: now, UpdatedAt: now,
	}
	store := &fakeObservationStore{
		radars: []workflow.Radar{
			{ID: "synthetic_radar_a", Lifecycle: workflow.RadarReady, ConfigVersion: "radar-a-v1", CreatedAt: now, UpdatedAt: now},
			{ID: "synthetic_radar_b", Lifecycle: workflow.RadarReady, ConfigVersion: "radar-b-v1", CreatedAt: now, UpdatedAt: now},
		},
		scans: []workflow.RadarScan{
			{ID: scanAID, RunID: uuid.New(), RadarID: "synthetic_radar_a", VolumeStartTime: now, VolumeEndTime: now, RadarConfigVersion: "radar-a-v1", Status: workflow.RadarScanGridReady, ScanCompleteness: &complete, MeanQualityIndex: &quality, CreatedAt: now, UpdatedAt: now},
			{ID: scanBID, RunID: uuid.New(), RadarID: "synthetic_radar_b", VolumeStartTime: now, VolumeEndTime: now, RadarConfigVersion: "radar-b-v1", Status: workflow.RadarScanFailed, DegradedReason: &failedReason, CreatedAt: now, UpdatedAt: now},
		},
		analysis: analysis,
		qc: workflow.RadarQCMetrics{
			ScanID: scanAID, RadarID: "synthetic_radar_a",
			QCProfile: "rp008-basic-v1", QCPipelineVersion: "rp008-basic-1.0.4",
			FlagDefinitionVersion: "qc-flags-v1", HealthState: workflow.RadarHealthHealthy,
			MeanQualityIndex: 0.82, ValidGateCount: 100, MissingGateCount: 5,
			ModuleStatuses: map[string]string{"radial_interference": "applied"}, MeasuredAt: now,
		},
		grid: workflow.RadarGridMetrics{
			ScanID: scanAID, RadarID: "synthetic_radar_a", GridID: "fuzhou-0p01-v1",
			GridConfigVersion: "fuzhou-0p01-v1", ProfileVersion: "rp009-hybrid-v1",
			AlgorithmVersion: "hybrid-scan-1.0.0", DEMAssetVersion: "ancillary-fujian-taiwan-v1",
			VerticalDatumStatus: "unverified_engineering", OperationalEligible: false,
			OperationalReasons: []string{"RADAR_CONFIG_DRAFT"}, GridCellCount: 100,
			ValidCellCount: 80, MissingCellCount: 20, ValidCoverageRatio: 0.8,
			MeanQualityIndex: 0.75, SelectionCounts: map[string]int64{"sweep_0": 80},
			SkippedSweeps: map[string]string{"sweep_1": "NO_VALID_DBZH"}, MeasuredAt: now,
		},
		qpe: workflow.AnalysisQPEMetrics{
			AnalysisID: analysis.ID, AnalysisTime: now, GridID: "fuzhou-0p01-v1",
			GridConfigVersion:      "fuzhou-0p01-v1",
			QPEConfigVersion:       "rp011-basic-qpe-v1",
			QPEAlgorithmVersion:    "basic-zr-qpe-1.0.0",
			MosaicConfigVersion:    "rp010-qi-mosaic-v1",
			MosaicAlgorithmVersion: "qi-mosaic-1.0.0",
			FlagDefinitionVersion:  "qc-flags-v1",
			InputMosaicURI:         "s3://rainpulse/mosaic.zarr", InputField: "DBZH_QC",
			CoefficientA: 200, ExponentB: 1.6, MaximumRateMMH: 300,
			GridCellCount: 100, ValidCellCount: 80, MissingCellCount: 20,
			NoRainCellCount: 40, RainCellCount: 40,
			ValidCoverageRatio: 0.8, MeanQualityIndex: 0.75,
			MeasuredAt: now,
		},
	}
	handler := api.NewHandler(api.Options{Version: "test", Observations: store})

	assertResponse := func(target string, want string) {
		t.Helper()
		request := httptest.NewRequest(http.MethodGet, target, nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), want) {
			t.Fatalf("GET %s: status=%d body=%s, missing %q", target, response.Code, response.Body.String(), want)
		}
	}

	assertResponse("/api/v1/radars", `"radar_id":"synthetic_radar_a"`)
	assertResponse("/api/v1/radars/status", `"radar_id":"synthetic_radar_a"`)
	assertResponse("/api/v1/radar-scans/"+scanBID.String(), `"status":"FAILED"`)
	assertResponse("/api/v1/radar-scans/"+scanAID.String()+"/qc-summary", `"qc_profile":"rp008-basic-v1"`)
	assertResponse("/api/v1/radar-scans/"+scanAID.String()+"/grid-summary", `"profile_version":"rp009-hybrid-v1"`)
	assertResponse("/api/v1/analysis-cycles/"+analysis.ID.String(), `"status":"ANALYSIS_READY"`)
	assertResponse("/api/v1/analysis-cycles/"+analysis.ID.String(), `"state":"FAILED"`)
	assertResponse("/api/v1/analysis-cycles/"+analysis.ID.String()+"/qpe-summary", `"qpe_config_version":"rp011-basic-qpe-v1"`)
}

func TestAnalysisEventStreamSendsDomainEvent(t *testing.T) {
	now := time.Date(2026, 8, 24, 3, 0, 0, 0, time.UTC)
	analysis := workflow.AnalysisCycle{
		ID:           uuid.MustParse("20000000-0000-4000-8000-000000000001"),
		RunID:        uuid.MustParse("20000000-0000-4000-8000-000000000002"),
		AnalysisTime: now, GridID: "grid", ConfigVersion: "config",
		Status: workflow.AnalysisReady, Radars: []workflow.AnalysisRadar{},
		CreatedAt: now, UpdatedAt: now,
	}
	handler := api.NewHandler(api.Options{
		Version: "test", Observations: &fakeObservationStore{analysis: analysis},
		SSEPollInterval: 5 * time.Millisecond,
	})
	server := httptest.NewServer(handler)
	defer server.Close()

	response, err := http.Get(server.URL + "/api/v1/events/stream?analysis_id=" + analysis.ID.String()) //nolint:noctx
	if err != nil {
		t.Fatalf("open analysis SSE stream: %v", err)
	}
	defer response.Body.Close()
	reader := bufio.NewReader(response.Body)
	var event strings.Builder
	for range 4 {
		line, err := reader.ReadString('\n')
		if err != nil {
			t.Fatalf("read analysis SSE event: %v", err)
		}
		event.WriteString(line)
	}
	if !strings.Contains(event.String(), "event: analysis.cycle.updated") ||
		!strings.Contains(event.String(), `"status":"ANALYSIS_READY"`) {
		t.Fatalf("unexpected analysis SSE event: %s", event.String())
	}
}

func TestAnalysisDiagnosticsExposeManifestAndOnlyListedPNGLayer(t *testing.T) {
	now := time.Date(2026, 8, 25, 12, 6, 0, 0, time.UTC)
	analysisID := uuid.MustParse("85000000-0000-4000-8000-000000000001")
	jobID := uuid.MustParse("83000000-0000-4000-8000-000000000001")
	unit := "mm/h"
	diagnostics := workflow.AnalysisDiagnostics{
		JobID:     jobID,
		BundleURI: "s3://rainpulse/diagnostics/fixture/diagnostics",
		Manifest: workflow.DiagnosticManifest{
			ContractVersion: "1.0", JobID: jobID, AnalysisID: analysisID,
			AnalysisTime: now, GridID: "fuzhou-grid",
			DiagnosticConfig:      "rp012-operational-diagnostics-v1",
			RendererVersion:       "radar-diagnostic-renderer-1.0.0",
			PaletteVersion:        "rainpulse-meteorological-v1",
			FlagDefinitionVersion: "qc-flags-v1",
			OperationalEligible:   false,
			OperationalReasons:    []string{"engineering_input"},
			CreatedAt:             now,
			Layers: []workflow.DiagnosticLayer{
				{
					LayerID: "grid-rate-qpe", Title: "瞬时雨强", Scope: "grid",
					Field: "RATE_QPE", Rendering: "scalar", Unit: &unit,
					ObjectPath: "layers/grid-rate-qpe.png", Width: 1002, Height: 402,
					PaletteVersion: "rainpulse-meteorological-v1",
					Bounds:         []float64{117.995, 24.995, 123.005, 27.005},
					Legend:         []workflow.DiagnosticLegendEntry{{Label: "≥ 0 mm/h", Color: "#dce9ee"}},
				},
			},
		},
	}
	store := &fakeObservationStore{diagnostics: diagnostics}
	reader := &fakeDiagnosticLayerReader{
		data: []byte("\x89PNG\r\n\x1a\nfixture"), etag: "fixture-etag",
	}
	handler := api.NewHandler(api.Options{
		Version: "test", Observations: store, DiagnosticLayers: reader,
	})

	manifestRequest := httptest.NewRequest(
		http.MethodGet,
		"/api/v1/analysis-cycles/"+analysisID.String()+"/diagnostics",
		nil,
	)
	manifestResponse := httptest.NewRecorder()
	handler.ServeHTTP(manifestResponse, manifestRequest)
	if manifestResponse.Code != http.StatusOK ||
		!strings.Contains(manifestResponse.Body.String(),
			`"image_url":"/api/v1/diagnostics/`+jobID.String()+`/layers/grid-rate-qpe"`) {
		t.Fatalf("unexpected diagnostic manifest: %d %s", manifestResponse.Code, manifestResponse.Body.String())
	}

	layerRequest := httptest.NewRequest(
		http.MethodGet,
		"/api/v1/diagnostics/"+jobID.String()+"/layers/grid-rate-qpe",
		nil,
	)
	layerResponse := httptest.NewRecorder()
	handler.ServeHTTP(layerResponse, layerRequest)
	if layerResponse.Code != http.StatusOK ||
		layerResponse.Header().Get("Content-Type") != "image/png" ||
		!strings.Contains(layerResponse.Header().Get("Cache-Control"), "immutable") ||
		reader.relativePath != "layers/grid-rate-qpe.png" {
		t.Fatalf("unexpected diagnostic layer response: %d %#v", layerResponse.Code, layerResponse.Header())
	}

	unknownRequest := httptest.NewRequest(
		http.MethodGet,
		"/api/v1/diagnostics/"+jobID.String()+"/layers/not-listed",
		nil,
	)
	unknownResponse := httptest.NewRecorder()
	handler.ServeHTTP(unknownResponse, unknownRequest)
	if unknownResponse.Code != http.StatusNotFound {
		t.Fatalf("unlisted diagnostic layer status = %d", unknownResponse.Code)
	}
}

type fakeRunStore struct {
	run  workflow.Run
	jobs []workflow.Job
	err  error
}

func (store *fakeRunStore) Ping(context.Context) error { return store.err }

func (store *fakeRunStore) LatestRun(context.Context) (workflow.Run, error) {
	return store.run, store.err
}

func (store *fakeRunStore) GetRun(_ context.Context, runID uuid.UUID) (workflow.Run, error) {
	if store.err != nil {
		return workflow.Run{}, store.err
	}
	if store.run.ID != runID {
		return workflow.Run{}, workflow.ErrNotFound
	}
	return store.run, nil
}

func (store *fakeRunStore) ListRuns(context.Context, int, *time.Time, *workflow.RunStatus) ([]workflow.Run, *time.Time, error) {
	return []workflow.Run{store.run}, nil, store.err
}

func (store *fakeRunStore) ListJobs(_ context.Context, runID uuid.UUID) ([]workflow.Job, error) {
	if runID != store.run.ID {
		return nil, workflow.ErrNotFound
	}
	return store.jobs, store.err
}

type fakeRunCommands struct {
	run    workflow.Run
	source uuid.UUID
}

func (commands *fakeRunCommands) Rerun(_ context.Context, source uuid.UUID) (workflow.Run, error) {
	commands.source = source
	return commands.run, nil
}

type fakeObservationStore struct {
	radars      []workflow.Radar
	scans       []workflow.RadarScan
	analysis    workflow.AnalysisCycle
	qc          workflow.RadarQCMetrics
	grid        workflow.RadarGridMetrics
	mosaic      workflow.AnalysisMosaicMetrics
	qpe         workflow.AnalysisQPEMetrics
	diagnostics workflow.AnalysisDiagnostics
}

func (store *fakeObservationStore) GetAnalysisDiagnostics(
	_ context.Context,
	analysisID uuid.UUID,
) (workflow.AnalysisDiagnostics, error) {
	if store.diagnostics.Manifest.AnalysisID != analysisID {
		return workflow.AnalysisDiagnostics{}, workflow.ErrNotFound
	}
	return store.diagnostics, nil
}

func (store *fakeObservationStore) GetDiagnosticLayer(
	_ context.Context,
	jobID uuid.UUID,
	layerID string,
) (string, string, error) {
	if store.diagnostics.JobID != jobID {
		return "", "", workflow.ErrNotFound
	}
	for _, layer := range store.diagnostics.Manifest.Layers {
		if layer.LayerID == layerID {
			return store.diagnostics.BundleURI, layer.ObjectPath, nil
		}
	}
	return "", "", workflow.ErrNotFound
}

func (store *fakeObservationStore) GetAnalysisMosaicMetrics(
	_ context.Context,
	_ uuid.UUID,
) (workflow.AnalysisMosaicMetrics, error) {
	return store.mosaic, nil
}

func (store *fakeObservationStore) GetAnalysisQPEMetrics(
	_ context.Context,
	_ uuid.UUID,
) (workflow.AnalysisQPEMetrics, error) {
	return store.qpe, nil
}

func (store *fakeObservationStore) GetRadarQCMetrics(
	_ context.Context,
	scanID uuid.UUID,
) (workflow.RadarQCMetrics, error) {
	if store.qc.ScanID != scanID {
		return workflow.RadarQCMetrics{}, workflow.ErrNotFound
	}
	return store.qc, nil
}

func (store *fakeObservationStore) GetRadarGridMetrics(
	_ context.Context,
	scanID uuid.UUID,
) (workflow.RadarGridMetrics, error) {
	if store.grid.ScanID != scanID {
		return workflow.RadarGridMetrics{}, workflow.ErrNotFound
	}
	return store.grid, nil
}

func (store *fakeObservationStore) ListRadars(context.Context) ([]workflow.Radar, error) {
	return store.radars, nil
}

func (store *fakeObservationStore) ListRadarStatuses(context.Context) ([]workflow.RadarStatusSummary, error) {
	statuses := make([]workflow.RadarStatusSummary, 0, len(store.radars))
	for _, radar := range store.radars {
		statuses = append(statuses, workflow.RadarStatusSummary{
			RadarID: radar.ID, DisplayName: radar.DisplayName,
			Lifecycle: radar.Lifecycle, ConfigVersion: radar.ConfigVersion,
			Health: workflow.RadarHealthHealthy,
		})
	}
	return statuses, nil
}

func (store *fakeObservationStore) GetRadar(_ context.Context, radarID string) (workflow.Radar, error) {
	for _, radar := range store.radars {
		if radar.ID == radarID {
			return radar, nil
		}
	}
	return workflow.Radar{}, workflow.ErrNotFound
}

func (store *fakeObservationStore) GetRadarStatus(_ context.Context, radarID string) (workflow.RadarStatusSummary, error) {
	return workflow.RadarStatusSummary{RadarID: radarID, Health: workflow.RadarHealthHealthy}, nil
}

func (store *fakeObservationStore) ListRadarScans(
	context.Context,
	int,
	*string,
	*workflow.RadarScanStatus,
) ([]workflow.RadarScan, error) {
	return store.scans, nil
}

func (store *fakeObservationStore) GetRadarScan(_ context.Context, scanID uuid.UUID) (workflow.RadarScan, error) {
	for _, scan := range store.scans {
		if scan.ID == scanID {
			return scan, nil
		}
	}
	return workflow.RadarScan{}, workflow.ErrNotFound
}

func (store *fakeObservationStore) ListAnalysisCycles(
	context.Context,
	int,
	*workflow.AnalysisStatus,
) ([]workflow.AnalysisCycle, error) {
	return []workflow.AnalysisCycle{store.analysis}, nil
}

func (store *fakeObservationStore) GetAnalysisCycle(_ context.Context, analysisID uuid.UUID) (workflow.AnalysisCycle, error) {
	if store.analysis.ID != analysisID {
		return workflow.AnalysisCycle{}, workflow.ErrNotFound
	}
	return store.analysis, nil
}

func floatPointer(value float64) *float64 {
	return &value
}

type fakeDiagnosticLayerReader struct {
	data         []byte
	etag         string
	artifactURI  string
	relativePath string
}

func (reader *fakeDiagnosticLayerReader) Read(
	_ context.Context,
	artifactURI string,
	relativePath string,
) ([]byte, string, error) {
	reader.artifactURI = artifactURI
	reader.relativePath = relativePath
	return reader.data, reader.etag, nil
}

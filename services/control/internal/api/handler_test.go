package api_test

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/api"
	ensembleproductstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/ensembleproducts"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	verificationstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/verification"
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
	if response.Header().Get("X-Content-Type-Options") != "nosniff" ||
		response.Header().Get("Content-Security-Policy") == "" {
		t.Fatal("API security headers are missing")
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

func TestListEndpointsRejectLimitsOutsideContract(t *testing.T) {
	handler := api.NewHandler(api.Options{
		Runs:         &fakeRunStore{},
		Observations: &fakeObservationStore{},
		Products:     &fakeProductStore{},
	})
	paths := []string{
		"/api/v1/runs?limit=0",
		"/api/v1/radar-scans?limit=-1",
		"/api/v1/analysis-cycles?limit=201",
		"/api/v1/products?limit=2147483647",
	}
	for _, target := range paths {
		t.Run(target, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, target, nil)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusBadRequest || !strings.Contains(response.Body.String(), "invalid_limit") {
				t.Fatalf("GET %s: status=%d body=%s", target, response.Code, response.Body.String())
			}
		})
	}
}

func TestAdministrativeEndpointsFailClosed(t *testing.T) {
	runID := uuid.MustParse("f3641335-13a3-4f68-96c0-56a5e0e684d7")
	target := "/api/v1/admin/runs/" + runID.String() + "/rerun"

	disabled := api.NewHandler(api.Options{})
	request := httptest.NewRequest(http.MethodPost, target, nil)
	response := httptest.NewRecorder()
	disabled.ServeHTTP(response, request)
	if response.Code != http.StatusForbidden || !strings.Contains(response.Body.String(), "admin_disabled") {
		t.Fatalf("disabled admin route: status=%d body=%s", response.Code, response.Body.String())
	}

	protected := api.NewHandler(api.Options{AdminToken: "test-admin-token"})
	request = httptest.NewRequest(http.MethodPost, target, nil)
	request.Header.Set("Authorization", "Bearer wrong-token")
	response = httptest.NewRecorder()
	protected.ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized || response.Header().Get("WWW-Authenticate") != "Bearer" {
		t.Fatalf("protected admin route: status=%d headers=%v", response.Code, response.Header())
	}
}

func TestRerunReportsUnsupportedRealWorkflowAsConflict(t *testing.T) {
	runID := uuid.MustParse("f3641335-13a3-4f68-96c0-56a5e0e684d7")
	handler := api.NewHandler(api.Options{
		AdminToken: "test-admin-token",
		Commands:   &fakeRunCommands{err: orchestration.ErrUnsupportedRerun},
	})
	request := httptest.NewRequest(http.MethodPost, "/api/v1/admin/runs/"+runID.String()+"/rerun", nil)
	request.Header.Set("Authorization", "Bearer test-admin-token")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusConflict || !strings.Contains(response.Body.String(), "unsupported_rerun") {
		t.Fatalf("unsupported rerun: status=%d body=%s", response.Code, response.Body.String())
	}
}

func TestAlgorithmVerificationEndpointsExposeFilteredEvidence(t *testing.T) {
	now := time.Date(2026, 8, 26, 8, 0, 0, 0, time.UTC)
	issueTime := time.Date(2021, 8, 10, 17, 0, 0, 0, time.UTC)
	meanDifference := 0.027
	fss := 0.72
	store := &fakeAlgorithmVerificationStore{
		detail: verificationstore.RunDetail{
			Run: verificationstore.RunSummary{
				ProfileVersion: "rp016-mrms-v1", RunID: "full-202108-v2",
				SchemaVersion: "1.0", PrimaryTruthKind: "observed_mrms_10min",
				CompletedIssueCount: 53, MetricRowCount: 57240,
				SkillStatus: "lk_supported", ModifiedAt: now,
			},
			Cases: []verificationstore.Case{{
				CaseID: "midwest_convection_20210810", Category: "wet",
				IssueTimes: []time.Time{issueTime},
			}},
			Filters: verificationstore.FilterOptions{
				Models:      []string{"lk", "persistence", "translation"},
				LeadMinutes: []int{10}, ThresholdsMMH: []float64{5}, WindowsPixels: []int{11},
				FSSScales: []verificationstore.FSSScale{{
					WindowPixels: 11, TargetKM: 10, ActualKMMin: 10.6, ActualKMMax: 11.4,
				}},
			},
			SkillSummary: verificationstore.SkillSummary{
				Status: "lk_supported", ComparisonMetric: "FSS",
				Comparisons: []verificationstore.SkillComparison{{
					Baseline: "persistence", BootstrapSampleCount: 2000,
					CaseMeanDifferences: map[string]float64{"midwest_convection_20210810": meanDifference},
					EvaluableCaseCount:  4, MaximumLeadMinutes: 60,
					MeanDifference95pctInterval: []*float64{&meanDifference, &meanDifference},
					MeanFSSDifference:           &meanDifference, PassesCaseGate: true,
					PositiveCaseCount: 4, ThresholdMMH: 5, TotalWetCaseCount: 4, WindowPixels: 11,
				}},
			},
		},
		metrics: []verificationstore.Metric{{
			CaseID: "midwest_convection_20210810", CaseCategory: "wet", IssueTime: issueTime,
			TruthKind: "observed_mrms_10min", Model: "lk", LeadMinutes: 10,
			ThresholdMMH: 5, WindowPixels: 11, WindowKM: 11.1, WindowTargetKM: 10, FSS: &fss,
		}},
		mapFrame: verificationstore.MapFrame{
			ContractVersion: "1.0", RendererVersion: "verification-renderer-v1",
			PaletteVersion: "rainfall-operational-v1", ProfileVersion: "rp016-mrms-v1",
			RunID: "full-202108-v2", CaseID: "midwest_convection_20210810",
			IssueTime: issueTime, ValidTime: issueTime.Add(10 * time.Minute), LeadMinutes: 10,
			TruthKind: "observed_mrms_10min", Projection: "EPSG:4326",
			PixelEdgeBounds: []float64{-95.005, 38.995, -89.995, 41.005},
			FitBounds:       []float64{-95, 39, -90, 41}, Width: 501, Height: 201,
			RainThresholdMMH: 0.1, ValidNoRainColor: "#dce6e2",
			Legend: []verificationstore.MapLegendEntry{{MinimumMMH: 0.1, Color: "#9dd9ff"}},
			Layers: []verificationstore.MapLayer{{
				AssetID: "lead-010-truth", Role: "truth", LeadMinutes: 10,
				ValidTime: issueTime.Add(10 * time.Minute), Width: 501, Height: 201,
				SHA256: strings.Repeat("a", 64), SizeBytes: 100, ValidCellCount: 100701,
			}},
			Motion: verificationstore.MapMotion{Unit: "grid_cells_per_5_minutes"},
		},
		mapAsset: verificationstore.MapAssetContent{
			Data: []byte("\x89PNG\r\n\x1a\nmap"), SHA256: strings.Repeat("a", 64),
		},
	}
	handler := api.NewHandler(api.Options{Version: "test", Verification: store})

	assert := func(target string, contains string) {
		t.Helper()
		request := httptest.NewRequest(http.MethodGet, target, nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), contains) {
			t.Fatalf("GET %s: status=%d body=%s", target, response.Code, response.Body.String())
		}
	}
	assert("/api/v1/algorithm-verification/runs", `"metric_row_count":57240`)
	assert("/api/v1/algorithm-verification/runs/rp016-mrms-v1/full-202108-v2", `"target_km":10`)
	assert(
		"/api/v1/algorithm-verification/runs/rp016-mrms-v1/full-202108-v2/metrics"+
			"?case_id=midwest_convection_20210810&issue_time=2021-08-10T17:00:00Z&threshold_mm_h=5&window_pixels=11",
		`"window_target_km":10`,
	)
	assert(
		"/api/v1/algorithm-verification/runs/rp016-mrms-v1/full-202108-v2/map-frame"+
			"?case_id=midwest_convection_20210810&issue_time=2021-08-10T17:00:00Z&lead_minutes=10",
		`"image_url":"/api/v1/algorithm-verification/runs/rp016-mrms-v1/full-202108-v2/map-assets/midwest_convection_20210810/20210810T170000Z/lead-010-truth"`,
	)
	mapRequest := httptest.NewRequest(
		http.MethodGet,
		"/api/v1/algorithm-verification/runs/rp016-mrms-v1/full-202108-v2/map-assets/"+
			"midwest_convection_20210810/20210810T170000Z/lead-010-truth",
		nil,
	)
	mapResponse := httptest.NewRecorder()
	handler.ServeHTTP(mapResponse, mapRequest)
	if mapResponse.Code != http.StatusOK || mapResponse.Header().Get("Content-Type") != "image/png" ||
		!strings.Contains(mapResponse.Header().Get("Cache-Control"), "immutable") {
		t.Fatalf("unexpected map asset response: status=%d headers=%v", mapResponse.Code, mapResponse.Header())
	}
	if store.filter.CaseID != "midwest_convection_20210810" || !store.filter.IssueTime.Equal(issueTime) {
		t.Fatalf("metric query was not delegated: %#v", store.filter)
	}
}

func TestOfflineEnsembleProductEndpointsExposeGISAssetsWithoutPublishing(t *testing.T) {
	bundleID := uuid.MustParse("9b000000-0000-4000-8000-000000000001")
	jobID := uuid.MustParse("9b000000-0000-4000-8000-000000000002")
	now := time.Date(2026, 8, 29, 2, 0, 0, 0, time.UTC)
	store := &fakeEnsembleProductStore{
		bundle: ensembleproductstore.Bundle{
			BundleID: bundleID, RunID: bundleID, JobID: jobID,
			IssueTime: now, GridID: "fuzhou_118_123_25_27_0p01deg_v1",
			PixelEdgeBounds: []float64{117.995, 24.995, 123.005, 27.005},
			Width:           501, Height: 201,
			SourceForecast: ensembleproductstore.SourceForecast{
				URI: "s3://rainpulse/forecast.zarr", SHA256: strings.Repeat("a", 64),
			},
			ModelID: "pysteps-steps", ModelVersion: "pysteps-steps-1.0.0",
			ModelConfigVersion:   "rp022-pysteps-steps-v1",
			ProductConfigVersion: "rp023-ensemble-application-products-v1",
			MemberCount:          12,
			CalibrationStatus:    "raw_ensemble_relative_frequency_uncalibrated",
			OperationalGate:      "independent_fujian_probabilistic_acceptance_required",
			CreatedAt:            now,
			Layers: []ensembleproductstore.Layer{{
				LayerID: "probability-gt-1", ProductType: "probability_exceedance",
				VariableName: "prob_gt_1", ThresholdMMH: floatPointer(1), Unit: "1",
				Legend: []ensembleproductstore.LegendEntry{{Minimum: 0.01, Color: "#d6eef7"}},
				Assets: []ensembleproductstore.Asset{{
					AssetID: "probability-gt-1-lead-005-png", AssetType: "rendered_png",
					MediaType: "image/png", SHA256: strings.Repeat("b", 64), SizeBytes: 12,
					LeadMinutes: 5, ValidTime: now.Add(5 * time.Minute), Unit: "1",
					CoverageRatio: 0.98, ValidCellCount: 98687, MissingCellCount: 2014,
				}},
			}},
		},
		asset: ensembleproductstore.AssetContent{
			Data: []byte("\x89PNG\r\n\x1a\nmap"), MediaType: "image/png",
			SHA256: strings.Repeat("b", 64), FileName: "layer.png",
		},
	}
	handler := api.NewHandler(api.Options{Version: "test", EnsembleProducts: store})

	request := httptest.NewRequest(http.MethodGet, "/api/v1/ensemble-products/latest", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK ||
		!strings.Contains(response.Body.String(), `"operational_eligible":false`) ||
		!strings.Contains(
			response.Body.String(),
			`/api/v1/ensemble-products/9b000000-0000-4000-8000-000000000001/assets/probability-gt-1-lead-005-png`,
		) {
		t.Fatalf("unexpected ensemble bundle response: status=%d body=%s", response.Code, response.Body.String())
	}

	request = httptest.NewRequest(
		http.MethodGet,
		"/api/v1/ensemble-products/"+bundleID.String()+"/assets/probability-gt-1-lead-005-png",
		nil,
	)
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK || response.Header().Get("Content-Type") != "image/png" ||
		!strings.Contains(response.Header().Get("Cache-Control"), "immutable") {
		t.Fatalf("unexpected ensemble asset response: status=%d headers=%v", response.Code, response.Header())
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
	handler := api.NewHandler(api.Options{
		Version: "test", Runs: store, Commands: commands, AdminToken: "test-admin-token",
	})

	assertStatus := func(method, target string, want int) *httptest.ResponseRecorder {
		t.Helper()
		request := httptest.NewRequest(method, target, nil)
		if strings.Contains(target, "/admin/") {
			request.Header.Set("Authorization", "Bearer test-admin-token")
		}
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
			GridConfigVersion: "fuzhou-0p01-v1", ProfileVersion: "rp016-hybrid-v1",
			AlgorithmVersion: "hybrid-scan-1.1.0", DEMAssetVersion: "ancillary-fujian-taiwan-v1",
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
			MosaicConfigVersion:    "rp016-qi-mosaic-v1",
			MosaicAlgorithmVersion: "qi-mosaic-1.1.0",
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
	assertResponse("/api/v1/radar-scans/"+scanAID.String()+"/grid-summary", `"profile_version":"rp016-hybrid-v1"`)
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

func TestProductCatalogContentAndIndexedQueries(t *testing.T) {
	now := time.Date(2026, 8, 25, 12, 10, 0, 0, time.UTC)
	productID := uuid.MustParse("98000000-0000-4000-8000-000000000001")
	pngID := uuid.MustParse("98000000-0000-4000-8000-000000000002")
	indexID := uuid.MustParse("98000000-0000-4000-8000-000000000003")
	validTimes := make([]time.Time, 24)
	for index := range validTimes {
		validTimes[index] = now.Add(time.Duration(index+1) * 5 * time.Minute)
	}
	product := workflow.Product{
		ID: productID, RunID: uuid.New(), ModelRunID: uuid.New(),
		ProductType: workflow.ProductRainRate,
		ModelID:     "pysteps-lk", ModelVersion: "pysteps-lk-1.1.0",
		ConfigVersion: "rp015-application-products-v1",
		GridID:        "tiny", IssueTime: now, ValidTimes: validTimes, MemberCount: 1,
		SourceForecastURI:    "s3://rainpulse/forecast.zarr",
		SourceForecastSHA256: strings.Repeat("a", 64), CreatedAt: now,
	}
	png := []byte("\x89PNG\r\n\x1a\nfixture")
	pngSHA := fmt.Sprintf("%x", sha256.Sum256(png))
	pointIndex := pointIndexFixture()
	pointSHA := fmt.Sprintf("%x", sha256.Sum256(pointIndex))
	coverage := 5.0 / 6.0
	lead := 5
	assets := []workflow.ProductAsset{
		{
			ID: pngID, ProductID: productID, AssetType: "rendered_png",
			ObjectURI: "s3://rainpulse/products/layer.png", MediaType: "image/png",
			SHA256: pngSHA, SizeBytes: int64(len(png)), LeadMinutes: &lead,
			ValidTime: &validTimes[0],
			Metadata: json.RawMessage(fmt.Sprintf(
				`{"unit":"mm h-1","coverage_ratio":%f,"valid_cell_count":5,"missing_cell_count":1,"no_rain_cell_count":1}`,
				coverage,
			)), CreatedAt: now,
		},
		{
			ID: indexID, ProductID: productID, AssetType: "point_query_index",
			ObjectURI: "s3://rainpulse/products/point-index.bin",
			MediaType: "application/vnd.rainpulse.point-index",
			SHA256:    pointSHA, SizeBytes: int64(len(pointIndex)), CreatedAt: now,
		},
	}
	store := &fakeProductStore{product: product, assets: assets}
	reader := &fakeProductObjectReader{objects: map[string][]byte{
		assets[0].ObjectURI: png, assets[1].ObjectURI: pointIndex,
	}}
	handler := api.NewHandler(api.Options{
		Version: "test", Products: store, ProductObjects: reader,
	})

	assert := func(target string, wantStatus int, want string) *httptest.ResponseRecorder {
		t.Helper()
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, target, nil))
		if response.Code != wantStatus || !strings.Contains(response.Body.String(), want) {
			t.Fatalf(
				"GET %s: status=%d body=%s missing=%q",
				target, response.Code, response.Body.String(), want,
			)
		}
		return response
	}
	assert(
		"/api/v1/products",
		http.StatusOK,
		`"source_forecast_sha256":"`+strings.Repeat("a", 64)+`"`,
	)
	assert(
		"/api/v1/products/"+productID.String()+"/assets",
		http.StatusOK,
		`"content_url":"/api/v1/products/`+productID.String()+
			`/assets/`+pngID.String()+`/content"`,
	)
	content := assert(
		"/api/v1/products/"+productID.String()+"/assets/"+pngID.String()+"/content",
		http.StatusOK,
		"fixture",
	)
	if content.Header().Get("ETag") != `"`+pngSHA+`"` ||
		content.Header().Get("Content-Type") != "image/png" {
		t.Fatalf("unexpected product content headers: %#v", content.Header())
	}
	assert(
		"/api/v1/point-forecast?product_id="+productID.String()+
			"&longitude=118.01&latitude=25.01",
		http.StatusOK,
		`"grid_longitude":118.01`,
	)
	area := assert(
		"/api/v1/area-statistics?product_id="+productID.String()+
			"&bbox=118,25,118.02,25.01&lead_time_minutes=5",
		http.StatusOK,
		`"missing_pixel_count":1`,
	)
	if !strings.Contains(
		area.Body.String(),
		fmt.Sprintf(`"valid_pixel_ratio":%g`, float32(coverage)),
	) {
		t.Fatalf("area response lost coverage ratio: %s", area.Body.String())
	}
}

func pointIndexFixture() []byte {
	const width, height, leads, recordBytes, headerBytes = 3, 2, 24, 5, 64
	data := make([]byte, headerBytes+width*height*leads*recordBytes)
	copy(data[:8], []byte{'R', 'P', 'P', 'N', 'T', 'V', '1', 0})
	binary.BigEndian.PutUint16(data[8:10], width)
	binary.BigEndian.PutUint16(data[10:12], height)
	binary.BigEndian.PutUint16(data[12:14], leads)
	binary.BigEndian.PutUint16(data[14:16], recordBytes)
	binary.BigEndian.PutUint64(data[16:24], math.Float64bits(118))
	binary.BigEndian.PutUint64(data[24:32], math.Float64bits(25))
	binary.BigEndian.PutUint64(data[32:40], math.Float64bits(0.01))
	binary.BigEndian.PutUint64(data[40:48], math.Float64bits(0.01))
	for cell := range width * height {
		for lead := range leads {
			offset := headerBytes + (cell*leads+lead)*recordBytes
			binary.BigEndian.PutUint32(
				data[offset:offset+4], math.Float32bits(float32(cell+lead)),
			)
			data[offset+4] = 254
		}
	}
	binary.BigEndian.PutUint32(data[64:68], math.Float32bits(float32(math.NaN())))
	data[68] = 255
	return data
}

type fakeRunStore struct {
	run  workflow.Run
	jobs []workflow.Job
	err  error
}

type fakeAlgorithmVerificationStore struct {
	detail    verificationstore.RunDetail
	metrics   []verificationstore.Metric
	filter    verificationstore.MetricFilter
	mapFrame  verificationstore.MapFrame
	mapFilter verificationstore.MapFrameFilter
	mapAsset  verificationstore.MapAssetContent
	err       error
}

func (store *fakeAlgorithmVerificationStore) ListRuns(context.Context) ([]verificationstore.RunSummary, error) {
	return []verificationstore.RunSummary{store.detail.Run}, store.err
}

func (store *fakeAlgorithmVerificationStore) GetRun(
	context.Context,
	string,
	string,
) (verificationstore.RunDetail, error) {
	return store.detail, store.err
}

func (store *fakeAlgorithmVerificationStore) ListMetrics(
	_ context.Context,
	_ string,
	_ string,
	filter verificationstore.MetricFilter,
) ([]verificationstore.Metric, error) {
	store.filter = filter
	return store.metrics, store.err
}

func (store *fakeAlgorithmVerificationStore) GetMapFrame(
	_ context.Context,
	_ string,
	_ string,
	filter verificationstore.MapFrameFilter,
) (verificationstore.MapFrame, error) {
	store.mapFilter = filter
	return store.mapFrame, store.err
}

func (store *fakeAlgorithmVerificationStore) ReadMapAsset(
	context.Context,
	string,
	string,
	string,
	string,
	string,
) (verificationstore.MapAssetContent, error) {
	return store.mapAsset, store.err
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
	err    error
}

func (commands *fakeRunCommands) Rerun(_ context.Context, source uuid.UUID) (workflow.Run, error) {
	commands.source = source
	return commands.run, commands.err
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

type fakeProductStore struct {
	product workflow.Product
	assets  []workflow.ProductAsset
}

func (store *fakeProductStore) ListProducts(
	context.Context,
	int,
	*time.Time,
	*uuid.UUID,
	*string,
	*workflow.ProductType,
) ([]workflow.Product, *time.Time, error) {
	return []workflow.Product{store.product}, nil, nil
}

func (store *fakeProductStore) GetProduct(
	_ context.Context,
	productID uuid.UUID,
) (workflow.Product, error) {
	if store.product.ID != productID {
		return workflow.Product{}, workflow.ErrNotFound
	}
	return store.product, nil
}

func (store *fakeProductStore) ListProductAssets(
	_ context.Context,
	productID uuid.UUID,
) ([]workflow.ProductAsset, error) {
	if store.product.ID != productID {
		return nil, workflow.ErrNotFound
	}
	return store.assets, nil
}

func (store *fakeProductStore) GetProductAsset(
	_ context.Context,
	productID uuid.UUID,
	assetID uuid.UUID,
) (workflow.ProductAsset, error) {
	for _, asset := range store.assets {
		if asset.ProductID == productID && asset.ID == assetID {
			return asset, nil
		}
	}
	return workflow.ProductAsset{}, workflow.ErrNotFound
}

type fakeProductObjectReader struct {
	objects map[string][]byte
}

type fakeEnsembleProductStore struct {
	bundle ensembleproductstore.Bundle
	asset  ensembleproductstore.AssetContent
	err    error
}

func (store *fakeEnsembleProductStore) GetLatest(
	context.Context,
) (ensembleproductstore.Bundle, error) {
	return store.bundle, store.err
}

func (store *fakeEnsembleProductStore) ReadAsset(
	context.Context,
	string,
	string,
) (ensembleproductstore.AssetContent, error) {
	return store.asset, store.err
}

func (reader *fakeProductObjectReader) ReadObject(
	_ context.Context,
	objectURI string,
	_ int64,
) ([]byte, string, error) {
	data, exists := reader.objects[objectURI]
	if !exists {
		return nil, "", workflow.ErrNotFound
	}
	return data, "fixture", nil
}

func (reader *fakeProductObjectReader) ReadRange(
	_ context.Context,
	objectURI string,
	offset int64,
	length int64,
) ([]byte, int64, string, error) {
	data, exists := reader.objects[objectURI]
	if !exists {
		return nil, 0, "", workflow.ErrNotFound
	}
	if offset < 0 || length < 0 || offset+length > int64(len(data)) {
		return nil, 0, "", fmt.Errorf("invalid fixture range")
	}
	return data[offset : offset+length], int64(len(data)), "fixture", nil
}

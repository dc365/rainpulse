package workspace

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestWorkspaceListCollapsesAnalysisAndForecastIntoOneCycle(t *testing.T) {
	core := fixtureCore(t)
	handler := &Handler{core: core, now: func() time.Time {
		return time.Date(2026, 9, 1, 1, 5, 0, 0, time.UTC)
	}}

	request := httptest.NewRequest(http.MethodGet, workspacePrefix, nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	var payload cycleList
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Items) != 1 {
		t.Fatalf("items = %d", len(payload.Items))
	}
	item := payload.Items[0]
	if !item.Capabilities.Radar || !item.Capabilities.LK || !item.Capabilities.Steps {
		t.Fatalf("capabilities = %+v", item.Capabilities)
	}
	if item.ExecutionMode != "realtime_shadow" {
		t.Fatalf("execution mode = %q", item.ExecutionMode)
	}
	if item.FreshnessSecond != 300 {
		t.Fatalf("freshness = %d", item.FreshnessSecond)
	}
}

func TestWorkspaceDetailReturnsStableFourPanelForecastLayout(t *testing.T) {
	handler := &Handler{core: fixtureCore(t), now: func() time.Time {
		return time.Date(2026, 9, 1, 1, 5, 0, 0, time.UTC)
	}}
	issue := time.Date(2026, 9, 1, 1, 0, 0, 0, time.UTC)
	request := httptest.NewRequest(
		http.MethodGet,
		workspacePrefix+"/"+encodeCycleID(defaultGridID, issue),
		nil,
	)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	var payload cycleDetail
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	for _, panelID := range []string{"qpe", "lk", "steps", "nowcastnet"} {
		if panelIndex(payload.Panels, panelID) < 0 {
			t.Fatalf("missing stable panel %s: %+v", panelID, payload.Panels)
		}
	}
	if got := payload.Panels[panelIndex(payload.Panels, "nowcastnet")].Status; got != "unavailable" {
		t.Fatalf("nowcastnet status = %q", got)
	}
	if got := len(payload.Panels[panelIndex(payload.Panels, "lk")].Frames); got != 2 {
		t.Fatalf("LK frames = %d", got)
	}
	if got := len(payload.Panels[panelIndex(payload.Panels, "steps")].Frames); got != 2 {
		t.Fatalf("STEPS frames = %d", got)
	}
	if len(payload.Radars) != 2 || payload.Radars[0].RadarID != "z9591" {
		t.Fatalf("radars = %+v", payload.Radars)
	}
	if len(payload.Timeline) != 3 {
		t.Fatalf("timeline = %+v", payload.Timeline)
	}
}

func TestWorkspaceDelegatesUnrelatedRoutes(t *testing.T) {
	core := http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.WriteHeader(http.StatusTeapot)
	})
	handler := NewHandler(core)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/system/status", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusTeapot {
		t.Fatalf("status = %d", response.Code)
	}
}

func fixtureCore(t *testing.T) http.Handler {
	t.Helper()
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		path := request.URL.Path
		switch {
		case path == "/api/v1/runs" && request.URL.Query().Get("status") == "PUBLISHED":
			writeFixture(response, `{"items":[{"run_id":"run-1","issue_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"PUBLISHED","execution_mode":"realtime_shadow"}]}`)
		case path == "/api/v1/runs":
			writeFixture(response, `{"items":[]}`)
		case path == "/api/v1/analysis-cycles":
			writeFixture(response, `{"items":[{"analysis_id":"analysis-1","run_id":"analysis-run-1","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/analysis.zarr","radar_count":2,"valid_coverage_ratio":0.5}]}`)
		case path == "/api/v1/ensemble-products/cycles":
			writeFixture(response, `[{"bundle_id":"bundle-1","run_id":"steps-run","issue_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","member_count":12}]`)
		case path == "/api/v1/analysis-cycles/analysis-1":
			writeFixture(response, `{"analysis_id":"analysis-1","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","radars":[{"radar_id":"z9593","state":"PARTICIPATING","scan_id":"scan-2","time_offset_seconds":12,"mean_quality_index":0.45},{"radar_id":"z9591","state":"PARTICIPATING","scan_id":"scan-1","time_offset_seconds":-20,"mean_quality_index":0.55}]}`)
		case path == "/api/v1/analysis-cycles/analysis-1/qpe-summary":
			writeFixture(response, `{"valid_coverage_ratio":0.5,"mean_quality_index":0.51,"maximum_observed_rate_mm_h":25.0,"p95_rate_mm_h":10.0}`)
		case path == "/api/v1/analysis-cycles/analysis-1/diagnostics":
			writeFixture(response, `{"analysis_time":"2026-09-01T01:00:00Z","layers":[{"layer_id":"qpe-layer","scope":"grid","field":"RATE_QPE","title":"QPE","image_url":"/qpe.png","unit":"mm/h","bounds":[117.995,24.995,123.005,27.005],"legend":[{"minimum":0.1,"color":"#fff"}]},{"layer_id":"raw-z9591","scope":"polar","field":"DBZH_RAW","radar_id":"z9591","title":"raw","image_url":"/raw.png","unit":"dBZ","legend":[]},{"layer_id":"qc-z9591","scope":"polar","field":"DBZH_QC","radar_id":"z9591","title":"qc","image_url":"/qc.png","unit":"dBZ","legend":[]}]}`)
		case path == "/api/v1/products":
			writeFixture(response, `{"items":[{"product_id":"product-1","product_type":"rain_rate","model_id":"pysteps-lk","model_version":"1.0","config_version":"shadow-v1"}]}`)
		case path == "/api/v1/products/product-1/assets":
			writeFixture(response, `[{"asset_id":"lk-5","asset_type":"rendered_png","content_url":"/lk5.png","media_type":"image/png","lead_time_minutes":5,"valid_time":"2026-09-01T01:05:00Z","unit":"mm/h"},{"asset_id":"lk-10","asset_type":"rendered_png","content_url":"/lk10.png","media_type":"image/png","lead_time_minutes":10,"valid_time":"2026-09-01T01:10:00Z","unit":"mm/h"}]`)
		case path == "/api/v1/ensemble-products/by-cycle":
			if request.URL.Query().Get("issue_time") != "2026-09-01T01:00:00Z" {
				t.Fatalf("unexpected issue time: %s", request.URL.RawQuery)
			}
			writeFixture(response, `{"bundle_id":"bundle-1","issue_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","member_count":12,"operational_eligible":false,"layers":[{"layer_id":"p50","product_type":"quantile","quantile":0.5,"unit":"mm/h","legend":[{"minimum":0.1,"color":"#fff"}],"assets":[{"asset_id":"steps-5","asset_type":"rendered_png","content_url":"/steps5.png","media_type":"image/png","lead_time_minutes":5,"valid_time":"2026-09-01T01:05:00Z","unit":"mm/h"},{"asset_id":"steps-10","asset_type":"rendered_png","content_url":"/steps10.png","media_type":"image/png","lead_time_minutes":10,"valid_time":"2026-09-01T01:10:00Z","unit":"mm/h"}]}]}`)
		default:
			http.Error(response, "fixture route not found: "+path+"?"+request.URL.RawQuery, http.StatusNotFound)
		}
	})
}

func writeFixture(response http.ResponseWriter, payload string) {
	_, _ = response.Write([]byte(strings.ReplaceAll(payload, `\"`, `"`)))
}

func TestWorkspaceConfiguredModeOverridesLegacyRunMode(t *testing.T) {
	mode := handlerExecutionMode(
		time.Date(2026, 9, 1, 1, 5, 0, 0, time.UTC),
		time.Date(2026, 9, 1, 1, 0, 0, 0, time.UTC),
		"realtime_shadow",
	)
	if mode != "realtime_shadow" {
		t.Fatalf("configured mode = %q", mode)
	}
}

func TestWorkspaceProxiesIngestStatusFailSoft(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/status" {
			t.Fatalf("unexpected upstream path %s", request.URL.Path)
		}
		writeFixture(response, `{"status":"ready","sources":[{"radar_id":"z9591"}]}`)
	}))
	defer upstream.Close()

	handler := &Handler{
		core: http.NotFoundHandler(), ingestStatusURL: upstream.URL + "/status",
		httpClient: upstream.Client(), now: time.Now,
	}
	request := httptest.NewRequest(http.MethodGet, ingestStatusPath, nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"z9591"`) {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
}

func TestWorkspaceAppliesMatchingNowcastNetShadowProbeStatus(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/status" {
			t.Fatalf("unexpected upstream path %s", request.URL.Path)
		}
		writeFixture(response, `{"status":"input_ineligible","reason":"spatial_shape_not_validated","profile_version":"fujian-nowcastnet-shadow-v1","issue_time":"2026-09-01T01:00:00Z","frame_count":9,"common_valid_ratio":1.0}`)
	}))
	defer upstream.Close()

	handler := &Handler{
		core: fixtureCore(t), nowcastNetStatusURL: upstream.URL + "/status",
		httpClient: upstream.Client(), now: func() time.Time {
			return time.Date(2026, 9, 1, 1, 5, 0, 0, time.UTC)
		},
	}
	issue := time.Date(2026, 9, 1, 1, 0, 0, 0, time.UTC)
	request := httptest.NewRequest(
		http.MethodGet,
		workspacePrefix+"/"+encodeCycleID(defaultGridID, issue),
		nil,
	)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	var payload cycleDetail
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	panel := payload.Panels[panelIndex(payload.Panels, "nowcastnet")]
	if panel.Status != "unavailable" || panel.UnavailableReason != "spatial_shape_not_validated" {
		t.Fatalf("nowcastnet panel = %+v", panel)
	}
	if panel.AlgorithmID != "fujian-nowcastnet-shadow-v1" {
		t.Fatalf("nowcastnet algorithm = %q", panel.AlgorithmID)
	}
}

func TestWorkspaceProxiesNowcastNetStatusFailSoft(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		writeFixture(response, `{"status":"waiting","reason":"missing_required_frame"}`)
	}))
	defer upstream.Close()

	handler := &Handler{
		core: http.NotFoundHandler(), nowcastNetStatusURL: upstream.URL + "/status",
		httpClient: upstream.Client(), now: time.Now,
	}
	request := httptest.NewRequest(http.MethodGet, nowcastNetStatusPath, nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"missing_required_frame"`) {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
}

func TestWorkspaceAddsFutureRadarAnalysesAsVerificationTruth(t *testing.T) {
	base := fixtureCore(t)
	core := http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/api/v1/analysis-cycles":
			writeFixture(response, `{"items":[{"analysis_id":"analysis-1","run_id":"analysis-run-1","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/analysis.zarr","radar_count":2},{"analysis_id":"analysis-2","run_id":"analysis-run-2","analysis_time":"2026-09-01T01:05:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/analysis-2.zarr","radar_count":2}]}`)
		case "/api/v1/analysis-cycles/analysis-2/diagnostics":
			writeFixture(response, `{"analysis_time":"2026-09-01T01:05:00Z","layers":[{"layer_id":"qpe-layer-2","scope":"grid","field":"RATE_QPE","title":"QPE","image_url":"/qpe-2.png","unit":"mm/h","bounds":[117.995,24.995,123.005,27.005],"legend":[{"minimum":0.1,"color":"#fff"}]}]}`)
		default:
			base.ServeHTTP(response, request)
		}
	})
	handler := &Handler{core: core, now: func() time.Time {
		return time.Date(2026, 9, 1, 1, 5, 0, 0, time.UTC)
	}}
	issue := time.Date(2026, 9, 1, 1, 0, 0, 0, time.UTC)
	request := httptest.NewRequest(
		http.MethodGet,
		workspacePrefix+"/"+encodeCycleID(defaultGridID, issue),
		nil,
	)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	var payload cycleDetail
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	qpe := payload.Panels[panelIndex(payload.Panels, "qpe")]
	if len(qpe.Frames) != 2 || qpe.Frames[1].ValidTime != "2026-09-01T01:05:00Z" {
		t.Fatalf("QPE truth frames = %+v", qpe.Frames)
	}
}

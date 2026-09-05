package workspace

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	nowcastnetproductstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/nowcastnetproducts"
	"github.com/google/uuid"
)

func TestWorkspaceAddsNativeCadenceNowcastNetShadowPanel(t *testing.T) {
	issueTime := time.Date(2026, 8, 28, 8, 30, 0, 0, time.UTC)
	bundleID := uuid.New()
	bundle := nowcastnetproductstore.Bundle{
		BundleID: bundleID, IssueTime: issueTime, ProfileVersion: "fujian-shadow-v1",
		MemberCount: 4, CadenceMinutes: 10, LegendUnit: "mm/h",
		Legend: []nowcastnetproductstore.LegendEntry{{Minimum: 0.1, Color: "#9dd9ff"}},
		Frames: []nowcastnetproductstore.Frame{{
			AssetID: "ensemble-mean-lead-010-png", MediaType: "image/png",
			LeadMinutes: 10, ValidTime: issueTime.Add(10 * time.Minute), Unit: "mm/h",
			SHA256: strings.Repeat("a", 64), CoverageRatio: 1, ValidCellCount: 64 * 128,
			Bounds: [4]float64{117.995, 25.965, 119.275, 26.605},
		}},
	}
	detail := cycleDetail{cycleSummary: cycleSummary{IssueTime: issueTime.Format(time.RFC3339)}}
	new(Handler).addNowcastNetProduct(&detail, bundle)

	if len(detail.Panels) != 1 || !detail.Capabilities.NowcastNet {
		t.Fatalf("panels = %+v, capabilities = %+v", detail.Panels, detail.Capabilities)
	}
	panel := detail.Panels[0]
	if panel.CadenceMinutes != 10 || panel.DisplayName != "NowcastNet（公开权重）" ||
		panel.Frames[0].Bounds == nil || panel.Frames[0].LeadMinutes != 10 {
		t.Fatalf("NowcastNet panel = %+v", panel)
	}
	wantURL := nowcastNetProductPrefix + "/" + bundleID.String() + "/assets/ensemble-mean-lead-010-png"
	if panel.Frames[0].ImageURL != wantURL {
		t.Fatalf("image URL = %q, want %q", panel.Frames[0].ImageURL, wantURL)
	}
}

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

func TestWorkspaceListCanExposeOnlyConfiguredAnalysisDateAndGrid(t *testing.T) {
	core := http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		switch {
		case request.URL.Path == "/api/v1/runs" && request.URL.Query().Get("status") == "PUBLISHED":
			writeFixture(response, `{"items":[{"run_id":"run-visible","issue_time":"2026-08-28T10:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"PUBLISHED"},{"run_id":"run-without-analysis","issue_time":"2026-08-28T11:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"PUBLISHED"},{"run_id":"run-old","issue_time":"2026-08-25T10:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"PUBLISHED"}]}`)
		case request.URL.Path == "/api/v1/runs":
			writeFixture(response, `{"items":[]}`)
		case request.URL.Path == "/api/v1/analysis-cycles":
			writeFixture(response, `{"items":[{"analysis_id":"analysis-visible","analysis_time":"2026-08-28T10:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/visible.zarr"},{"analysis_id":"analysis-synthetic","analysis_time":"2026-08-28T09:00:00Z","grid_id":"rp004-synthetic-grid","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/synthetic.zarr"},{"analysis_id":"analysis-old","analysis_time":"2026-08-24T10:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/old.zarr"}]}`)
		case request.URL.Path == "/api/v1/ensemble-products/cycles":
			writeFixture(response, `[]`)
		default:
			http.Error(response, "fixture route not found", http.StatusNotFound)
		}
	})
	handler := &Handler{
		core:                 core,
		now:                  func() time.Time { return time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC) },
		catalogGridID:        defaultGridID,
		catalogDateUTC:       "2026-08-28",
		catalogNeedsAnalysis: true,
	}

	request := httptest.NewRequest(http.MethodGet, workspacePrefix+"?limit=200", nil)
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
		t.Fatalf("items = %+v", payload.Items)
	}
	if payload.Items[0].IssueTime != "2026-08-28T10:00:00Z" || payload.Items[0].AnalysisID == "" {
		t.Fatalf("visible cycle = %+v", payload.Items[0])
	}
}

func TestWorkspacePrefersLatestRegeneratedForecastForDuplicateCycle(t *testing.T) {
	old := forecastRun{RunID: "source", CreatedAt: "2026-09-01T08:00:00Z"}
	regenerated := forecastRun{RunID: "regenerated", CreatedAt: "2026-09-02T08:00:00Z"}
	if !preferForecast(&old, regenerated) {
		t.Fatal("newer regenerated forecast was not preferred")
	}
	if preferForecast(&regenerated, old) {
		t.Fatal("older source forecast replaced the regenerated forecast")
	}
}

func TestWorkspaceCatalogReadsEveryForecastRunPage(t *testing.T) {
	const cursor = "2026-08-28T10:00:00Z"
	core := http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		switch {
		case request.URL.Path == "/api/v1/runs" && request.URL.Query().Get("status") == "PUBLISHED" && request.URL.Query().Get("cursor") == "":
			writeFixture(response, `{"items":[{"run_id":"run-newer","issue_time":"2026-08-28T10:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"PUBLISHED"}],"next_cursor":"2026-08-28T10:00:00Z"}`)
		case request.URL.Path == "/api/v1/runs" && request.URL.Query().Get("status") == "PUBLISHED" && request.URL.Query().Get("cursor") == cursor:
			writeFixture(response, `{"items":[{"run_id":"run-older","issue_time":"2026-08-28T09:55:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"PUBLISHED"}]}`)
		case request.URL.Path == "/api/v1/runs":
			writeFixture(response, `{"items":[]}`)
		case request.URL.Path == "/api/v1/analysis-cycles":
			writeFixture(response, `{"items":[{"analysis_id":"analysis-newer","analysis_time":"2026-08-28T10:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/newer.zarr"},{"analysis_id":"analysis-older","analysis_time":"2026-08-28T09:55:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/older.zarr"}]}`)
		case request.URL.Path == "/api/v1/ensemble-products/cycles":
			writeFixture(response, `[]`)
		default:
			http.Error(response, "fixture route not found", http.StatusNotFound)
		}
	})
	handler := &Handler{core: core, now: func() time.Time {
		return time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	}}

	request := httptest.NewRequest(http.MethodGet, workspacePrefix+"?limit=200", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	var payload cycleList
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Items) != 2 {
		t.Fatalf("items = %+v", payload.Items)
	}
	for _, item := range payload.Items {
		if !item.Capabilities.LK || item.RunID == "" {
			t.Fatalf("paged forecast missing from catalog: %+v", item)
		}
	}
}

func TestWorkspacePrefersLatestAnalysisForDuplicateHistoricalTimes(t *testing.T) {
	core := http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		switch {
		case request.URL.Path == "/api/v1/runs":
			writeFixture(response, `{"items":[]}`)
		case request.URL.Path == "/api/v1/analysis-cycles":
			writeFixture(response, `{"items":[
				{"analysis_id":"rp040-1100","analysis_time":"2026-08-28T03:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","config_version":"rp040-fujian-four-radar-qc-v2","created_at":"2026-08-31T08:26:30Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/rp040/1100.zarr"},
				{"analysis_id":"rp008-1100","analysis_time":"2026-08-28T03:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","config_version":"rp034-fujian-four-radar-engineering-v1","created_at":"2026-08-30T18:50:00Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/rp008/1100.zarr"},
				{"analysis_id":"rp040-1055","analysis_time":"2026-08-28T02:55:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","config_version":"rp040-fujian-four-radar-qc-v2","created_at":"2026-08-31T08:26:20Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/rp040/1055.zarr"},
				{"analysis_id":"rp008-1055","analysis_time":"2026-08-28T02:55:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","config_version":"rp034-fujian-four-radar-engineering-v1","created_at":"2026-08-30T18:50:00Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/rp008/1055.zarr"},
				{"analysis_id":"rp040-1050","analysis_time":"2026-08-28T02:50:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","config_version":"rp040-fujian-four-radar-qc-v2","created_at":"2026-08-31T08:26:10Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/rp040/1050.zarr"},
				{"analysis_id":"rp008-1050","analysis_time":"2026-08-28T02:50:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","config_version":"rp034-fujian-four-radar-engineering-v1","created_at":"2026-08-30T18:50:00Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/rp008/1050.zarr"}
			]}`)
		case request.URL.Path == "/api/v1/ensemble-products/cycles":
			writeFixture(response, `[]`)
		default:
			http.Error(response, "fixture route not found", http.StatusNotFound)
		}
	})
	handler := &Handler{core: core, now: func() time.Time {
		return time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	}}

	request := httptest.NewRequest(http.MethodGet, workspacePrefix+"?limit=200", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	var payload cycleList
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	want := map[string]string{
		"2026-08-28T02:50:00Z": "rp040-1050",
		"2026-08-28T02:55:00Z": "rp040-1055",
		"2026-08-28T03:00:00Z": "rp040-1100",
	}
	if len(payload.Items) != len(want) {
		t.Fatalf("items = %+v", payload.Items)
	}
	for _, item := range payload.Items {
		if item.AnalysisID != want[item.IssueTime] {
			t.Errorf("analysis at %s = %q, want %q", item.IssueTime, item.AnalysisID, want[item.IssueTime])
		}
	}
}

func TestWorkspaceHistoricalRP040CyclesExposeCurrentQPELineage(t *testing.T) {
	type historicalCase struct {
		analysisID string
		issueTime  string
		p95        float64
		maximum    float64
	}
	cases := []historicalCase{
		{analysisID: "rp040-1100", issueTime: "2026-08-28T03:00:00Z", p95: 8.0465, maximum: 143.089},
		{analysisID: "rp040-1055", issueTime: "2026-08-28T02:55:00Z", p95: 8.0465, maximum: 115.307},
		{analysisID: "rp040-1050", issueTime: "2026-08-28T02:50:00Z", p95: 8.0465, maximum: 190.812},
	}
	byID := make(map[string]historicalCase, len(cases))
	for _, item := range cases {
		byID[item.analysisID] = item
	}
	core := http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		path := request.URL.Path
		switch {
		case path == "/api/v1/runs":
			writeFixture(response, `{"items":[]}`)
		case path == "/api/v1/ensemble-products/cycles":
			writeFixture(response, `[]`)
		case path == "/api/v1/analysis-cycles":
			items := make([]map[string]any, 0, len(cases)*2)
			for _, item := range cases {
				items = append(items,
					map[string]any{
						"analysis_id": item.analysisID, "analysis_time": item.issueTime,
						"grid_id": defaultGridID, "config_version": "rp040-fujian-four-radar-qc-v2",
						"created_at": "2026-08-31T08:26:30Z", "status": "ANALYSIS_READY",
						"analysis_uri": "s3://rainpulse/rp040/" + item.analysisID + "/analysis.zarr",
					},
					map[string]any{
						"analysis_id": "rp008-" + item.analysisID, "analysis_time": item.issueTime,
						"grid_id": defaultGridID, "config_version": "rp034-fujian-four-radar-engineering-v1",
						"created_at": "2026-08-30T18:50:00Z", "status": "ANALYSIS_READY",
						"analysis_uri": "s3://rainpulse/rp008/" + item.analysisID + "/analysis.zarr",
					},
				)
			}
			_ = json.NewEncoder(response).Encode(map[string]any{"items": items})
		default:
			matched := false
			for analysisID, item := range byID {
				base := "/api/v1/analysis-cycles/" + analysisID
				mosaicURI := "s3://rainpulse/rp040/" + analysisID + "/mosaic.zarr"
				switch path {
				case base:
					matched = true
					_ = json.NewEncoder(response).Encode(map[string]any{
						"analysis_id": analysisID, "analysis_time": item.issueTime,
						"grid_id": defaultGridID, "config_version": "rp040-fujian-four-radar-qc-v2",
						"created_at": "2026-08-31T08:26:30Z", "mosaic_uri": mosaicURI,
						"radars": []any{},
					})
				case base + "/qpe-summary":
					matched = true
					_ = json.NewEncoder(response).Encode(map[string]any{
						"analysis_id": analysisID, "analysis_time": item.issueTime,
						"grid_id":                  defaultGridID,
						"qpe_config_version":       "rp011-basic-qpe-v1",
						"mosaic_config_version":    "rp040-fujian-four-radar-qc-v2",
						"mosaic_algorithm_version": "qi-mosaic-1.3.1-rp040-qc",
						"input_mosaic_uri":         mosaicURI,
						"valid_coverage_ratio":     0.42, "mean_quality_index": 0.5,
						"maximum_observed_rate_mm_h": item.maximum, "p95_rate_mm_h": item.p95,
					})
				case base + "/diagnostics":
					matched = true
					_ = json.NewEncoder(response).Encode(map[string]any{
						"analysis_time": item.issueTime,
						"layers": []map[string]any{{
							"layer_id": "qpe-" + analysisID, "scope": "grid", "field": "RATE_QPE",
							"title": "QPE", "image_url": "/" + analysisID + ".png", "unit": "mm/h",
							"bounds": []float64{117.995, 24.995, 123.005, 27.005},
						}},
					})
				}
			}
			if !matched {
				http.Error(response, "fixture route not found: "+path, http.StatusNotFound)
			}
		}
	})
	handler := &Handler{core: core, now: func() time.Time {
		return time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	}}

	for _, item := range cases {
		item := item
		t.Run(item.analysisID, func(t *testing.T) {
			issueTime, err := time.Parse(time.RFC3339, item.issueTime)
			if err != nil {
				t.Fatal(err)
			}
			request := httptest.NewRequest(
				http.MethodGet,
				workspacePrefix+"/"+encodeCycleID(defaultGridID, issueTime),
				nil,
			)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusOK {
				t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
			}
			var payload struct {
				AnalysisID string `json:"analysis_id"`
				Quality    struct {
					P95     *float64 `json:"p95_rate_mm_h"`
					Maximum *float64 `json:"maximum_rate_mm_h"`
				} `json:"quality"`
				Trace struct {
					AnalysisConfigVersion  string `json:"analysis_config_version"`
					MosaicAlgorithmVersion string `json:"mosaic_algorithm_version"`
					InputMosaicURI         string `json:"input_mosaic_uri"`
					QPEConfigVersion       string `json:"qpe_config_version"`
				} `json:"analysis_trace"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
				t.Fatal(err)
			}
			if payload.AnalysisID != item.analysisID {
				t.Fatalf("analysis ID = %q", payload.AnalysisID)
			}
			if payload.Quality.P95 == nil || *payload.Quality.P95 != item.p95 ||
				payload.Quality.Maximum == nil || *payload.Quality.Maximum != item.maximum {
				t.Fatalf("quality = %+v", payload.Quality)
			}
			if payload.Trace.AnalysisConfigVersion != "rp040-fujian-four-radar-qc-v2" ||
				payload.Trace.MosaicAlgorithmVersion != "qi-mosaic-1.3.1-rp040-qc" ||
				payload.Trace.QPEConfigVersion != "rp011-basic-qpe-v1" ||
				payload.Trace.InputMosaicURI != "s3://rainpulse/rp040/"+item.analysisID+"/mosaic.zarr" {
				t.Fatalf("analysis trace = %+v", payload.Trace)
			}
		})
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
	qcPanel := payload.Panels[panelIndex(payload.Panels, "dbzh_qc:z9591")]
	if qcPanel.DisplayName != "Z9591 业务质控反射率" {
		t.Fatalf("QC display name = %q", qcPanel.DisplayName)
	}
}

func TestWorkspaceRejectsQPEFromAnotherAnalysisLineage(t *testing.T) {
	base := fixtureCore(t)
	core := http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/api/v1/analysis-cycles/analysis-1/qpe-summary" {
			writeFixture(response, `{"analysis_id":"stale-analysis","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","valid_coverage_ratio":0.9,"mean_quality_index":0.9,"maximum_observed_rate_mm_h":300,"p95_rate_mm_h":200}`)
			return
		}
		base.ServeHTTP(response, request)
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
	lineageWarning := false
	for _, warning := range payload.Warnings {
		lineageWarning = lineageWarning || warning == "qpe-lineage"
	}
	if payload.Quality.P95RateMMH != nil || !lineageWarning {
		t.Fatalf("quality = %+v, warnings = %+v", payload.Quality, payload.Warnings)
	}
	if qpe := payload.Panels[panelIndex(payload.Panels, "qpe")]; qpe.Status != "unavailable" {
		t.Fatalf("QPE panel = %+v", qpe)
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

func TestWorkspaceUsesClearlyLabelledReferenceScanForMissingRadar(t *testing.T) {
	base := fixtureCore(t)
	core := http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		switch request.URL.Path {
		case "/api/v1/analysis-cycles":
			writeFixture(response, `{"items":[
{"analysis_id":"analysis-missing","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/a.zarr","radars":[{"radar_id":"z9591","state":"MISSING"}]},
{"analysis_id":"analysis-reference","analysis_time":"2026-09-01T01:05:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/b.zarr","radars":[{"radar_id":"z9591","state":"PARTICIPATING","scan_id":"scan-reference","time_offset_seconds":-180}]}
]}`)
		case "/api/v1/analysis-cycles/analysis-missing":
			writeFixture(response, `{"analysis_id":"analysis-missing","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","radars":[{"radar_id":"z9591","state":"MISSING"}]}`)
		case "/api/v1/analysis-cycles/analysis-missing/qpe-summary":
			writeFixture(response, `{}`)
		case "/api/v1/analysis-cycles/analysis-missing/diagnostics":
			writeFixture(response, `{"analysis_time":"2026-09-01T01:00:00Z","layers":[{"layer_id":"qpe","scope":"grid","field":"RATE_QPE","image_url":"/qpe.png"}]}`)
		case "/api/v1/analysis-cycles/analysis-reference/diagnostics":
			writeFixture(response, `{"analysis_time":"2026-09-01T01:05:00Z","layers":[
{"layer_id":"raw-reference","scope":"polar","field":"DBZH_RAW","radar_id":"z9591","scan_id":"scan-reference","image_url":"/raw-reference.png","unit":"dBZ"},
{"layer_id":"qc-reference","scope":"polar","field":"DBZH_QC","radar_id":"z9591","scan_id":"scan-reference","image_url":"/qc-reference.png","unit":"dBZ"}
]}`)
		default:
			base.ServeHTTP(response, request)
		}
	})
	handler := &Handler{core: core, now: func() time.Time {
		return time.Date(2026, 9, 1, 1, 10, 0, 0, time.UTC)
	}}
	issue := time.Date(2026, 9, 1, 1, 0, 0, 0, time.UTC)
	request := httptest.NewRequest(http.MethodGet, workspacePrefix+"/"+encodeCycleID(defaultGridID, issue), nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	var payload cycleDetail
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	panel := payload.Panels[panelIndex(payload.Panels, "dbzh_raw:z9591")]
	if panel.Lifecycle != "reference" || len(panel.Frames) != 1 || !panel.Frames[0].ReferenceObservation ||
		panel.Frames[0].ObservationTime != "2026-09-01T01:02:00Z" || panel.Frames[0].ObservationOffsetSeconds == nil || *panel.Frames[0].ObservationOffsetSeconds != 120 {
		t.Fatalf("reference radar panel = %+v", panel)
	}
}

func TestWorkspaceFallsBackWhenLatestAnalysisHasNoDiagnostics(t *testing.T) {
	const issueTime = "2026-09-01T01:00:00Z"
	core := http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		switch {
		case request.URL.Path == "/api/v1/runs":
			writeFixture(response, `{"items":[]}`)
		case request.URL.Path == "/api/v1/analysis-cycles":
			writeFixture(response, `{"items":[
{"analysis_id":"analysis-incomplete","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","created_at":"2026-09-03T00:00:00Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/incomplete.zarr"},
{"analysis_id":"analysis-complete","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","created_at":"2026-09-02T00:00:00Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/complete.zarr"},
{"analysis_id":"analysis-future-incomplete","analysis_time":"2026-09-01T01:05:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","created_at":"2026-09-03T00:00:00Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/future-incomplete.zarr"},
{"analysis_id":"analysis-future-complete","analysis_time":"2026-09-01T01:05:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","created_at":"2026-09-02T00:00:00Z","status":"ANALYSIS_READY","analysis_uri":"s3://rainpulse/future-complete.zarr"}
]}`)
		case request.URL.Path == "/api/v1/ensemble-products/cycles":
			writeFixture(response, `[]`)
		case request.URL.Path == "/api/v1/analysis-cycles/analysis-incomplete":
			writeFixture(response, `{"analysis_id":"analysis-incomplete","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1"}`)
		case request.URL.Path == "/api/v1/analysis-cycles/analysis-incomplete/qpe-summary":
			writeFixture(response, `{"analysis_id":"analysis-incomplete","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1"}`)
		case request.URL.Path == "/api/v1/analysis-cycles/analysis-incomplete/diagnostics":
			http.Error(response, "diagnostics unavailable", http.StatusNotFound)
		case request.URL.Path == "/api/v1/analysis-cycles/analysis-complete":
			writeFixture(response, `{"analysis_id":"analysis-complete","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1"}`)
		case request.URL.Path == "/api/v1/analysis-cycles/analysis-complete/qpe-summary":
			writeFixture(response, `{"analysis_id":"analysis-complete","analysis_time":"2026-09-01T01:00:00Z","grid_id":"fuzhou_118_123_25_27_0p01deg_v1","valid_coverage_ratio":0.5}`)
		case request.URL.Path == "/api/v1/analysis-cycles/analysis-complete/diagnostics":
			writeFixture(response, `{"analysis_time":"2026-09-01T01:00:00Z","layers":[{"layer_id":"qpe","scope":"grid","field":"RATE_QPE","image_url":"/qpe.png","unit":"mm/h","bounds":[117.995,24.995,123.005,27.005]}]}`)
		case request.URL.Path == "/api/v1/analysis-cycles/analysis-future-incomplete/diagnostics":
			http.Error(response, "diagnostics unavailable", http.StatusNotFound)
		case request.URL.Path == "/api/v1/analysis-cycles/analysis-future-complete/diagnostics":
			writeFixture(response, `{"analysis_time":"2026-09-01T01:05:00Z","layers":[{"layer_id":"qpe-future","scope":"grid","field":"RATE_QPE","image_url":"/qpe-future.png","unit":"mm/h","bounds":[117.995,24.995,123.005,27.005]}]}`)
		default:
			http.Error(response, "fixture route not found", http.StatusNotFound)
		}
	})
	handler := &Handler{core: core, now: func() time.Time {
		return time.Date(2026, 9, 3, 1, 0, 0, 0, time.UTC)
	}}
	request := httptest.NewRequest(http.MethodGet, workspacePrefix+"/"+encodeCycleID(defaultGridID, mustTime(t, issueTime)), nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	var payload cycleDetail
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	qpeIndex := panelIndex(payload.Panels, "qpe")
	if payload.AnalysisID != "analysis-complete" || qpeIndex < 0 || len(payload.Panels[qpeIndex].Frames) != 2 || payload.Panels[qpeIndex].Frames[0].LeadMinutes != 0 || payload.Panels[qpeIndex].Frames[1].LeadMinutes != 5 {
		t.Fatalf("analysis = %q, panels = %+v", payload.AnalysisID, payload.Panels)
	}
	if !containsString(payload.Warnings, "analysis-fallback") {
		t.Fatalf("warnings = %+v", payload.Warnings)
	}
}

func mustTime(t *testing.T, value string) time.Time {
	t.Helper()
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

package workspace

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	workspaceContractVersion = "1.0"
	workspacePrefix          = "/api/v1/workspace/cycles"
	ingestStatusPath         = "/api/v1/workspace/ingest-status"
	nowcastNetStatusPath     = "/api/v1/workspace/nowcastnet-shadow-status"
	defaultGridID            = "fuzhou_118_123_25_27_0p01deg_v1"
)

var (
	defaultGridBounds   = [4]float64{118, 25, 123, 27}
	defaultRasterBounds = [4]float64{117.995, 24.995, 123.005, 27.005}
)

// Handler adds a read-only, UI-oriented workspace projection in front of the
// existing domain API. The original handler remains authoritative; this layer
// deliberately composes its bounded JSON responses instead of reaching into
// PostgreSQL or object storage directly.
type Handler struct {
	core                 http.Handler
	now                  func() time.Time
	executionMode        string
	catalogGridID        string
	catalogDateUTC       string
	catalogNeedsAnalysis bool
	ingestStatusURL      string
	nowcastNetStatusURL  string
	httpClient           *http.Client
}

// NewHandler wraps the existing control API with the unified workspace routes.
func NewHandler(core http.Handler) http.Handler {
	mode := strings.TrimSpace(os.Getenv("RAINPULSE_WORKSPACE_EXECUTION_MODE"))
	if mode != "realtime_shadow" && mode != "operational" {
		mode = ""
	}
	catalogDateUTC := strings.TrimSpace(os.Getenv("RAINPULSE_WORKSPACE_CYCLE_DATE_UTC"))
	if _, err := time.Parse(time.DateOnly, catalogDateUTC); err != nil {
		catalogDateUTC = ""
	}
	return &Handler{
		core: core, executionMode: mode,
		catalogGridID:  strings.TrimSpace(os.Getenv("RAINPULSE_WORKSPACE_CYCLE_GRID_ID")),
		catalogDateUTC: catalogDateUTC,
		catalogNeedsAnalysis: strings.EqualFold(
			strings.TrimSpace(os.Getenv("RAINPULSE_WORKSPACE_REQUIRE_ANALYSIS")),
			"true",
		),
		ingestStatusURL: strings.TrimSpace(os.Getenv("RAINPULSE_INGEST_STATUS_URL")),
		nowcastNetStatusURL: strings.TrimSpace(
			os.Getenv("RAINPULSE_NOWCASTNET_SHADOW_STATUS_URL"),
		),
		httpClient: &http.Client{Timeout: 3 * time.Second},
		now:        func() time.Time { return time.Now().UTC() },
	}
}

func (handler *Handler) ServeHTTP(response http.ResponseWriter, request *http.Request) {
	if request.Method == http.MethodGet && request.URL.Path == nowcastNetStatusPath {
		handler.getNowcastNetStatus(response, request)
		return
	}
	if request.Method == http.MethodGet && request.URL.Path == ingestStatusPath {
		handler.getIngestStatus(response, request)
		return
	}
	if request.Method == http.MethodGet && request.URL.Path == workspacePrefix {
		handler.listCycles(response, request)
		return
	}
	if request.Method == http.MethodGet && strings.HasPrefix(request.URL.Path, workspacePrefix+"/") {
		handler.getCycle(response, request)
		return
	}
	handler.core.ServeHTTP(response, request)
}

type cycleSummary struct {
	CycleID         string            `json:"cycle_id"`
	IssueTime       string            `json:"issue_time"`
	GridID          string            `json:"grid_id"`
	ExecutionMode   string            `json:"execution_mode"`
	FreshnessSecond int64             `json:"freshness_seconds"`
	Capabilities    cycleCapabilities `json:"capabilities"`
	AnalysisID      string            `json:"analysis_id,omitempty"`
	RunID           string            `json:"run_id,omitempty"`
	EnsembleID      string            `json:"ensemble_bundle_id,omitempty"`
}

type cycleCapabilities struct {
	Radar      bool `json:"radar"`
	LK         bool `json:"lk"`
	Steps      bool `json:"steps"`
	NowcastNet bool `json:"nowcastnet"`
}

type cycleList struct {
	SchemaVersion   string         `json:"schema_version"`
	Items           []cycleSummary `json:"items"`
	GeneratedAt     string         `json:"generated_at"`
	DegradedSources []string       `json:"degraded_sources,omitempty"`
}

type gridView struct {
	GridID       string     `json:"grid_id"`
	Bounds       [4]float64 `json:"bounds"`
	RasterBounds [4]float64 `json:"raster_bounds"`
}

type qualitySummary struct {
	CoverageRatio    *float64 `json:"coverage_ratio,omitempty"`
	MeanQualityIndex *float64 `json:"mean_quality_index,omitempty"`
	MaximumRateMMH   *float64 `json:"maximum_rate_mm_h,omitempty"`
	P95RateMMH       *float64 `json:"p95_rate_mm_h,omitempty"`
}

type radarView struct {
	RadarID           string   `json:"radar_id"`
	State             string   `json:"state"`
	ScanID            string   `json:"scan_id,omitempty"`
	TimeOffsetSeconds *int     `json:"time_offset_seconds,omitempty"`
	MeanQualityIndex  *float64 `json:"mean_quality_index,omitempty"`
}

type legendEntry struct {
	Minimum *float64 `json:"minimum,omitempty"`
	Label   string   `json:"label,omitempty"`
	Color   string   `json:"color"`
}

type frameView struct {
	AssetID        string      `json:"asset_id"`
	ValidTime      string      `json:"valid_time"`
	LeadMinutes    int         `json:"lead_time_minutes"`
	ImageURL       string      `json:"image_url"`
	MediaType      string      `json:"media_type"`
	Unit           string      `json:"unit,omitempty"`
	SHA256         string      `json:"sha256,omitempty"`
	CoverageRatio  *float64    `json:"coverage_ratio,omitempty"`
	ValidCellCount *int        `json:"valid_cell_count,omitempty"`
	MissingCount   *int        `json:"missing_cell_count,omitempty"`
	Bounds         *[4]float64 `json:"bounds,omitempty"`
}

type panelView struct {
	PanelID           string        `json:"panel_id"`
	AlgorithmID       string        `json:"algorithm_id"`
	DisplayName       string        `json:"display_name"`
	Role              string        `json:"role"`
	Lifecycle         string        `json:"lifecycle"`
	DataKind          string        `json:"data_kind"`
	CadenceMinutes    int           `json:"cadence_minutes"`
	Status            string        `json:"status"`
	UnavailableReason string        `json:"unavailable_reason,omitempty"`
	RadarID           string        `json:"radar_id,omitempty"`
	LegendUnit        string        `json:"legend_unit,omitempty"`
	Legend            []legendEntry `json:"legend,omitempty"`
	Frames            []frameView   `json:"frames"`
}

type cycleDetail struct {
	SchemaVersion string `json:"schema_version"`
	cycleSummary
	Grid          gridView       `json:"grid"`
	Quality       qualitySummary `json:"quality"`
	AnalysisTrace *analysisTrace `json:"analysis_trace,omitempty"`
	Radars        []radarView    `json:"radars"`
	Timeline      []string       `json:"timeline"`
	Panels        []panelView    `json:"panels"`
	Warnings      []string       `json:"warnings,omitempty"`
}

type analysisTrace struct {
	AnalysisID             string `json:"analysis_id"`
	AnalysisConfigVersion  string `json:"analysis_config_version,omitempty"`
	AnalysisCreatedAt      string `json:"analysis_created_at,omitempty"`
	MosaicConfigVersion    string `json:"mosaic_config_version,omitempty"`
	MosaicAlgorithmVersion string `json:"mosaic_algorithm_version,omitempty"`
	InputMosaicURI         string `json:"input_mosaic_uri,omitempty"`
	QPEConfigVersion       string `json:"qpe_config_version,omitempty"`
}

type forecastRunPage struct {
	Items []forecastRun `json:"items"`
}

type forecastRun struct {
	RunID         string `json:"run_id"`
	IssueTime     string `json:"issue_time"`
	GridID        string `json:"grid_id"`
	Status        string `json:"status"`
	ExecutionMode string `json:"execution_mode"`
}

type analysisCyclePage struct {
	Items []analysisCycle `json:"items"`
}

type analysisCycle struct {
	AnalysisID     string          `json:"analysis_id"`
	RunID          string          `json:"run_id"`
	AnalysisTime   string          `json:"analysis_time"`
	GridID         string          `json:"grid_id"`
	ConfigVersion  string          `json:"config_version"`
	CreatedAt      string          `json:"created_at"`
	Status         string          `json:"status"`
	MosaicURI      *string         `json:"mosaic_uri"`
	AnalysisURI    *string         `json:"analysis_uri"`
	RadarCount     int             `json:"radar_count"`
	CoverageRatio  *float64        `json:"valid_coverage_ratio"`
	Radars         []analysisRadar `json:"radars"`
	Operational    bool            `json:"operational_eligible"`
	DegradedReason string          `json:"degraded_reason"`
}

type analysisRadar struct {
	RadarID           string   `json:"radar_id"`
	State             string   `json:"state"`
	ScanID            *string  `json:"scan_id"`
	TimeOffsetSeconds *int     `json:"time_offset_seconds"`
	MeanQualityIndex  *float64 `json:"mean_quality_index"`
}

type ensembleCycle struct {
	BundleID    string `json:"bundle_id"`
	RunID       string `json:"run_id"`
	IssueTime   string `json:"issue_time"`
	GridID      string `json:"grid_id"`
	MemberCount int    `json:"member_count"`
}

type diagnosticBundle struct {
	AnalysisTime string            `json:"analysis_time"`
	Layers       []diagnosticLayer `json:"layers"`
}

type diagnosticLayer struct {
	LayerID  string             `json:"layer_id"`
	Scope    string             `json:"scope"`
	Field    string             `json:"field"`
	RadarID  string             `json:"radar_id"`
	Title    string             `json:"title"`
	ImageURL string             `json:"image_url"`
	Unit     *string            `json:"unit"`
	Bounds   []float64          `json:"bounds"`
	Legend   []diagnosticLegend `json:"legend"`
}

type diagnosticLegend struct {
	Minimum *float64 `json:"minimum"`
	Label   string   `json:"label"`
	Color   string   `json:"color"`
}

type qpeSummary struct {
	AnalysisID             string   `json:"analysis_id"`
	AnalysisTime           string   `json:"analysis_time"`
	GridID                 string   `json:"grid_id"`
	QPEConfigVersion       string   `json:"qpe_config_version"`
	MosaicConfigVersion    string   `json:"mosaic_config_version"`
	MosaicAlgorithmVersion string   `json:"mosaic_algorithm_version"`
	InputMosaicURI         string   `json:"input_mosaic_uri"`
	CoverageRatio          *float64 `json:"valid_coverage_ratio"`
	MeanQualityIndex       *float64 `json:"mean_quality_index"`
	MaximumRateMMH         *float64 `json:"maximum_observed_rate_mm_h"`
	P95RateMMH             *float64 `json:"p95_rate_mm_h"`
}

type productPage struct {
	Items []product `json:"items"`
}

type product struct {
	ProductID     string `json:"product_id"`
	ProductType   string `json:"product_type"`
	ModelID       string `json:"model_id"`
	ModelVersion  string `json:"model_version"`
	ConfigVersion string `json:"config_version"`
}

type productAsset struct {
	AssetID        string   `json:"asset_id"`
	AssetType      string   `json:"asset_type"`
	ContentURL     string   `json:"content_url"`
	MediaType      string   `json:"media_type"`
	SHA256         string   `json:"sha256"`
	LeadMinutes    *int     `json:"lead_time_minutes"`
	ValidTime      string   `json:"valid_time"`
	Unit           *string  `json:"unit"`
	CoverageRatio  *float64 `json:"coverage_ratio"`
	ValidCellCount *int     `json:"valid_cell_count"`
	MissingCount   *int     `json:"missing_cell_count"`
}

type ensembleBundle struct {
	BundleID            string          `json:"bundle_id"`
	RunID               string          `json:"run_id"`
	IssueTime           string          `json:"issue_time"`
	GridID              string          `json:"grid_id"`
	MemberCount         int             `json:"member_count"`
	OperationalEligible bool            `json:"operational_eligible"`
	Layers              []ensembleLayer `json:"layers"`
}

type ensembleLayer struct {
	LayerID      string           `json:"layer_id"`
	ProductType  string           `json:"product_type"`
	ThresholdMMH *float64         `json:"threshold_mm_h"`
	Quantile     *float64         `json:"quantile"`
	Unit         *string          `json:"unit"`
	Legend       []ensembleLegend `json:"legend"`
	Assets       []ensembleAsset  `json:"assets"`
}

type ensembleLegend struct {
	Minimum float64 `json:"minimum"`
	Color   string  `json:"color"`
	Label   string  `json:"label"`
}

type ensembleAsset struct {
	AssetID        string   `json:"asset_id"`
	AssetType      string   `json:"asset_type"`
	ContentURL     string   `json:"content_url"`
	MediaType      string   `json:"media_type"`
	SHA256         string   `json:"sha256"`
	LeadMinutes    *int     `json:"lead_time_minutes"`
	ValidTime      string   `json:"valid_time"`
	Unit           *string  `json:"unit"`
	CoverageRatio  *float64 `json:"coverage_ratio"`
	ValidCellCount *int     `json:"valid_cell_count"`
	MissingCount   *int     `json:"missing_cell_count"`
}

type cycleAccumulator struct {
	summary  cycleSummary
	run      *forecastRun
	analysis *analysisCycle
	ensemble *ensembleCycle
}

type nowcastNetShadowStatus struct {
	Status                    string         `json:"status"`
	Reason                    string         `json:"reason"`
	CheckedAt                 string         `json:"checked_at"`
	ProfileVersion            string         `json:"profile_version"`
	IssueTime                 string         `json:"issue_time"`
	RequiredFrameTimes        []string       `json:"required_frame_times"`
	FrameCount                int            `json:"frame_count"`
	CommonValidRatio          float64        `json:"common_valid_ratio"`
	ROI                       map[string]int `json:"roi"`
	InferenceEnabled          bool           `json:"inference_enabled"`
	SpatialShapeValidated     bool           `json:"spatial_shape_validated"`
	ProductPublicationEnabled bool           `json:"product_publication_enabled"`
	OperationalEligible       bool           `json:"operational_eligible"`
}

func (handler *Handler) getIngestStatus(response http.ResponseWriter, request *http.Request) {
	handler.proxyStatus(
		response,
		request,
		handler.ingestStatusURL,
		"ingest_status_not_configured",
		"ingest_status_request_failed",
		"ingest_status_invalid",
	)
}

func (handler *Handler) getNowcastNetStatus(response http.ResponseWriter, request *http.Request) {
	handler.proxyStatus(
		response,
		request,
		handler.nowcastNetStatusURL,
		"nowcastnet_shadow_status_not_configured",
		"nowcastnet_shadow_status_request_failed",
		"nowcastnet_shadow_status_invalid",
	)
}

func (handler *Handler) proxyStatus(
	response http.ResponseWriter,
	request *http.Request,
	upstreamURL string,
	notConfiguredReason string,
	requestFailedReason string,
	invalidReason string,
) {
	if upstreamURL == "" {
		writeJSON(response, http.StatusServiceUnavailable, map[string]any{
			"status": "unavailable", "reason": notConfiguredReason,
		})
		return
	}
	var payload any
	if err := handler.readRemoteJSON(request.Context(), upstreamURL, &payload); err != nil {
		reason := requestFailedReason
		if errors.Is(err, errInvalidRemoteStatus) {
			reason = invalidReason
		}
		writeJSON(response, http.StatusServiceUnavailable, map[string]any{
			"status": "unavailable", "reason": reason,
		})
		return
	}
	writeJSON(response, http.StatusOK, payload)
}

var errInvalidRemoteStatus = errors.New("invalid remote status")

func (handler *Handler) readRemoteJSON(ctx context.Context, upstreamURL string, target any) error {
	client := handler.httpClient
	if client == nil {
		client = &http.Client{Timeout: 3 * time.Second}
	}
	upstream, err := http.NewRequestWithContext(ctx, http.MethodGet, upstreamURL, nil)
	if err != nil {
		return err
	}
	result, err := client.Do(upstream)
	if err != nil {
		return err
	}
	defer result.Body.Close()
	if result.StatusCode < 200 || result.StatusCode >= 300 {
		return errInvalidRemoteStatus
	}
	if err := json.NewDecoder(result.Body).Decode(target); err != nil {
		return fmt.Errorf("%w: %v", errInvalidRemoteStatus, err)
	}
	return nil
}

func (handler *Handler) listCycles(response http.ResponseWriter, request *http.Request) {
	catalog, degraded := handler.catalog(request.Context())
	items := make([]cycleSummary, 0, len(catalog))
	for _, item := range catalog {
		if !handler.catalogCycleVisible(item.summary) {
			continue
		}
		items = append(items, item.summary)
	}
	sort.Slice(items, func(left, right int) bool {
		return items[left].IssueTime > items[right].IssueTime
	})
	limit := queryLimit(request.URL.Query().Get("limit"), 100, 500)
	if len(items) > limit {
		items = items[:limit]
	}
	writeJSON(response, http.StatusOK, cycleList{
		SchemaVersion: workspaceContractVersion,
		Items:         items, GeneratedAt: handler.now().Format(time.RFC3339), DegradedSources: degraded,
	})
}

func (handler *Handler) catalogCycleVisible(item cycleSummary) bool {
	if handler.catalogGridID != "" && item.GridID != handler.catalogGridID {
		return false
	}
	if handler.catalogNeedsAnalysis && item.AnalysisID == "" {
		return false
	}
	if handler.catalogDateUTC == "" {
		return true
	}
	issueTime, ok := normalizedTime(item.IssueTime)
	return ok && issueTime.Format(time.DateOnly) == handler.catalogDateUTC
}

func (handler *Handler) getCycle(response http.ResponseWriter, request *http.Request) {
	rawID := strings.TrimPrefix(request.URL.Path, workspacePrefix+"/")
	gridID, issueTime, err := decodeCycleID(rawID)
	if err != nil {
		writeError(response, http.StatusBadRequest, err)
		return
	}
	catalog, degraded := handler.catalog(request.Context())
	item := catalog[cycleKey(gridID, issueTime)]
	if item == nil {
		writeError(response, http.StatusNotFound, errors.New("workspace cycle not found"))
		return
	}
	detail := cycleDetail{
		SchemaVersion: workspaceContractVersion,
		cycleSummary:  item.summary,
		Grid:          gridView{GridID: gridID, Bounds: defaultGridBounds, RasterBounds: defaultRasterBounds},
		Warnings:      append([]string(nil), degraded...),
	}
	if gridID != defaultGridID {
		detail.Grid.Bounds = [4]float64{}
		detail.Grid.RasterBounds = [4]float64{}
	}

	if item.analysis != nil {
		handler.addAnalysis(request.Context(), &detail, *item.analysis)
		handler.addObservedTimeline(
			request.Context(),
			&detail,
			catalog,
			*item.analysis,
		)
	}
	if item.run != nil {
		handler.addForecastProducts(request.Context(), &detail, *item.run)
	}
	if item.ensemble != nil {
		handler.addEnsemble(request.Context(), &detail, *item.ensemble)
	}
	ensureStablePanels(&detail)
	handler.applyNowcastNetShadowStatus(request.Context(), &detail)
	finalizeTimeline(&detail)
	writeJSON(response, http.StatusOK, detail)
}

func (handler *Handler) catalog(ctx context.Context) (map[string]*cycleAccumulator, []string) {
	catalog := make(map[string]*cycleAccumulator)
	degraded := make([]string, 0)
	seenRuns := make(map[string]struct{})
	for _, status := range []string{"PUBLISHED", "VERIFYING", "VERIFIED"} {
		var page forecastRunPage
		path := "/api/v1/runs?status=" + url.QueryEscape(status) + "&limit=100"
		if err := handler.readCore(ctx, path, &page); err != nil {
			degraded = append(degraded, "runs:"+strings.ToLower(status))
			continue
		}
		for index := range page.Items {
			run := page.Items[index]
			if _, duplicate := seenRuns[run.RunID]; duplicate {
				continue
			}
			seenRuns[run.RunID] = struct{}{}
			parsed, ok := normalizedTime(run.IssueTime)
			if !ok || run.GridID == "" {
				continue
			}
			entry := accumulator(catalog, run.GridID, parsed, handler.now(), handler.executionMode)
			copy := run
			entry.run = &copy
			entry.summary.RunID = run.RunID
			entry.summary.Capabilities.LK = true
			if handler.executionMode == "" && run.ExecutionMode != "" {
				entry.summary.ExecutionMode = run.ExecutionMode
			}
		}
	}

	var analyses analysisCyclePage
	if err := handler.readCore(ctx, "/api/v1/analysis-cycles?status=ANALYSIS_READY&limit=200", &analyses); err != nil {
		degraded = append(degraded, "analysis-cycles")
	} else {
		for index := range analyses.Items {
			analysis := analyses.Items[index]
			parsed, ok := normalizedTime(analysis.AnalysisTime)
			if !ok || analysis.GridID == "" || analysis.AnalysisURI == nil {
				continue
			}
			entry := accumulator(catalog, analysis.GridID, parsed, handler.now(), handler.executionMode)
			if !preferAnalysis(entry.analysis, analysis) {
				continue
			}
			copy := analysis
			entry.analysis = &copy
			entry.summary.AnalysisID = analysis.AnalysisID
			entry.summary.Capabilities.Radar = true
		}
	}

	var ensembles []ensembleCycle
	if err := handler.readCore(ctx, "/api/v1/ensemble-products/cycles", &ensembles); err != nil {
		degraded = append(degraded, "ensemble-products")
	} else {
		for index := range ensembles {
			ensemble := ensembles[index]
			parsed, ok := normalizedTime(ensemble.IssueTime)
			if !ok || ensemble.GridID == "" {
				continue
			}
			entry := accumulator(catalog, ensemble.GridID, parsed, handler.now(), handler.executionMode)
			copy := ensemble
			entry.ensemble = &copy
			entry.summary.EnsembleID = ensemble.BundleID
			entry.summary.Capabilities.Steps = true
		}
	}
	return catalog, uniqueStrings(degraded)
}

func preferAnalysis(existing *analysisCycle, candidate analysisCycle) bool {
	if existing == nil {
		return true
	}
	candidateCreatedAt, candidateOK := normalizedTime(candidate.CreatedAt)
	existingCreatedAt, existingOK := normalizedTime(existing.CreatedAt)
	if candidateOK != existingOK {
		return candidateOK
	}
	return candidateOK && candidateCreatedAt.After(existingCreatedAt)
}

func accumulator(
	catalog map[string]*cycleAccumulator,
	gridID string,
	issueTime time.Time,
	now time.Time,
	executionMode string,
) *cycleAccumulator {
	key := cycleKey(gridID, issueTime)
	if existing := catalog[key]; existing != nil {
		return existing
	}
	freshness := now.Sub(issueTime).Seconds()
	if freshness < 0 {
		freshness = 0
	}
	mode := handlerExecutionMode(now, issueTime, executionMode)
	item := &cycleAccumulator{summary: cycleSummary{
		CycleID: encodeCycleID(gridID, issueTime), IssueTime: issueTime.Format(time.RFC3339),
		GridID: gridID, ExecutionMode: mode, FreshnessSecond: int64(freshness),
	}}
	catalog[key] = item
	return item
}

func handlerExecutionMode(now time.Time, issueTime time.Time, configured string) string {
	if configured != "" {
		return configured
	}
	if now.Sub(issueTime) <= 15*time.Minute {
		return "realtime"
	}
	return "historical"
}

func (handler *Handler) addAnalysis(ctx context.Context, detail *cycleDetail, summary analysisCycle) {
	var full analysisCycle
	if err := handler.readCore(ctx, "/api/v1/analysis-cycles/"+url.PathEscape(summary.AnalysisID), &full); err != nil {
		detail.Warnings = append(detail.Warnings, "analysis-detail")
		full = summary
	}
	detail.AnalysisTrace = &analysisTrace{
		AnalysisID:            full.AnalysisID,
		AnalysisConfigVersion: full.ConfigVersion,
		AnalysisCreatedAt:     full.CreatedAt,
	}
	for _, radar := range full.Radars {
		item := radarView{RadarID: radar.RadarID, State: radar.State,
			TimeOffsetSeconds: radar.TimeOffsetSeconds, MeanQualityIndex: radar.MeanQualityIndex}
		if radar.ScanID != nil {
			item.ScanID = *radar.ScanID
		}
		detail.Radars = append(detail.Radars, item)
	}
	sort.Slice(detail.Radars, func(i, j int) bool { return detail.Radars[i].RadarID < detail.Radars[j].RadarID })

	var qpe qpeSummary
	if err := handler.readCore(ctx, "/api/v1/analysis-cycles/"+url.PathEscape(summary.AnalysisID)+"/qpe-summary", &qpe); err != nil {
		detail.Warnings = append(detail.Warnings, "qpe-summary")
	} else {
		detail.AnalysisTrace.MosaicConfigVersion = qpe.MosaicConfigVersion
		detail.AnalysisTrace.MosaicAlgorithmVersion = qpe.MosaicAlgorithmVersion
		detail.AnalysisTrace.InputMosaicURI = qpe.InputMosaicURI
		detail.AnalysisTrace.QPEConfigVersion = qpe.QPEConfigVersion
		if !qpeLineageMatches(full, qpe) {
			detail.Warnings = append(detail.Warnings, "qpe-lineage")
			return
		}
		detail.Quality = qualitySummary{CoverageRatio: qpe.CoverageRatio,
			MeanQualityIndex: qpe.MeanQualityIndex, MaximumRateMMH: qpe.MaximumRateMMH,
			P95RateMMH: qpe.P95RateMMH}
	}

	var diagnostics diagnosticBundle
	if err := handler.readCore(ctx, "/api/v1/analysis-cycles/"+url.PathEscape(summary.AnalysisID)+"/diagnostics", &diagnostics); err != nil {
		detail.Warnings = append(detail.Warnings, "analysis-diagnostics")
		return
	}
	for _, layer := range diagnostics.Layers {
		panel, ok := diagnosticPanel(layer, summary.AnalysisTime)
		if !ok {
			continue
		}
		detail.Panels = append(detail.Panels, panel)
		if layer.Scope == "grid" && layer.Field == "RATE_QPE" {
			if bounds, valid := fourBounds(layer.Bounds); valid {
				detail.Grid.RasterBounds = bounds
			}
		}
	}
}

func qpeLineageMatches(analysis analysisCycle, qpe qpeSummary) bool {
	if qpe.AnalysisID != "" && qpe.AnalysisID != analysis.AnalysisID {
		return false
	}
	if qpe.GridID != "" && qpe.GridID != analysis.GridID {
		return false
	}
	if qpe.AnalysisTime != "" {
		qpeTime, qpeOK := normalizedTime(qpe.AnalysisTime)
		analysisTime, analysisOK := normalizedTime(analysis.AnalysisTime)
		if !qpeOK || !analysisOK || !qpeTime.Equal(analysisTime) {
			return false
		}
	}
	if analysis.MosaicURI != nil && qpe.InputMosaicURI != "" &&
		qpe.InputMosaicURI != *analysis.MosaicURI {
		return false
	}
	return qpe.MosaicConfigVersion == "" || analysis.ConfigVersion == "" ||
		qpe.MosaicConfigVersion == analysis.ConfigVersion
}

func (handler *Handler) addObservedTimeline(
	ctx context.Context,
	detail *cycleDetail,
	catalog map[string]*cycleAccumulator,
	selected analysisCycle,
) {
	issueTime, ok := normalizedTime(selected.AnalysisTime)
	if !ok {
		return
	}
	type candidate struct {
		time     time.Time
		analysis analysisCycle
	}
	candidates := make([]candidate, 0, 24)
	for _, entry := range catalog {
		if entry.analysis == nil || entry.analysis.AnalysisID == selected.AnalysisID ||
			entry.analysis.GridID != selected.GridID {
			continue
		}
		validTime, valid := normalizedTime(entry.analysis.AnalysisTime)
		if !valid || !validTime.After(issueTime) || validTime.After(issueTime.Add(2*time.Hour)) {
			continue
		}
		candidates = append(candidates, candidate{time: validTime, analysis: *entry.analysis})
	}
	sort.Slice(candidates, func(left, right int) bool {
		return candidates[left].time.Before(candidates[right].time)
	})
	for _, item := range candidates {
		var diagnostics diagnosticBundle
		path := "/api/v1/analysis-cycles/" + url.PathEscape(item.analysis.AnalysisID) + "/diagnostics"
		if err := handler.readCore(ctx, path, &diagnostics); err != nil {
			continue
		}
		for _, layer := range diagnostics.Layers {
			if layer.Scope != "grid" || layer.Field != "RATE_QPE" {
				continue
			}
			panel, accepted := diagnosticPanel(layer, item.analysis.AnalysisTime)
			if !accepted {
				break
			}
			panel.Frames[0].LeadMinutes = int(item.time.Sub(issueTime) / time.Minute)
			mergePanelFrame(detail, panel)
			break
		}
	}
}

func mergePanelFrame(detail *cycleDetail, panel panelView) {
	if len(panel.Frames) != 1 {
		return
	}
	index := panelIndex(detail.Panels, panel.PanelID)
	if index < 0 {
		detail.Panels = append(detail.Panels, panel)
		return
	}
	frame := panel.Frames[0]
	frameTime, frameTimeOK := normalizedTime(frame.ValidTime)
	for _, existing := range detail.Panels[index].Frames {
		existingTime, existingTimeOK := normalizedTime(existing.ValidTime)
		if frameTimeOK && existingTimeOK && frameTime.Equal(existingTime) {
			return
		}
	}
	detail.Panels[index].Frames = append(detail.Panels[index].Frames, frame)
	sortFrames(detail.Panels[index].Frames)
}

func diagnosticPanel(layer diagnosticLayer, validTime string) (panelView, bool) {
	if layer.ImageURL == "" || layer.LayerID == "" {
		return panelView{}, false
	}
	role := "diagnostic"
	panelID := "analysis:" + strings.ToLower(layer.Field)
	display := layer.Title
	dataKind := "diagnostic"
	if layer.Scope == "polar" {
		role = "qc"
		dataKind = "reflectivity"
		panelID = strings.ToLower(layer.Field) + ":" + strings.ToLower(layer.RadarID)
		if layer.Field == "DBZH_RAW" {
			display = strings.ToUpper(layer.RadarID) + " 原始反射率"
		} else if layer.Field == "DBZH_QC" {
			display = strings.ToUpper(layer.RadarID) + " 业务质控反射率"
		}
	} else if layer.Field == "RATE_QPE" {
		role = "observation"
		dataKind = "rain_rate"
		panelID = "qpe"
		display = "雷达 QPE"
	}
	legend := make([]legendEntry, 0, len(layer.Legend))
	for _, item := range layer.Legend {
		legend = append(legend, legendEntry{Minimum: item.Minimum, Label: item.Label, Color: item.Color})
	}
	frame := frameView{AssetID: layer.LayerID, ValidTime: validTime, LeadMinutes: 0,
		ImageURL: layer.ImageURL, MediaType: "image/png"}
	if layer.Unit != nil {
		frame.Unit = *layer.Unit
	}
	if bounds, ok := fourBounds(layer.Bounds); ok {
		frame.Bounds = &bounds
	}
	return panelView{PanelID: panelID, AlgorithmID: "radar-analysis", DisplayName: display,
		Role: role, Lifecycle: "analysis", DataKind: dataKind, CadenceMinutes: 5,
		Status: "ready", RadarID: layer.RadarID, LegendUnit: frame.Unit,
		Legend: legend, Frames: []frameView{frame}}, true
}

func (handler *Handler) addForecastProducts(ctx context.Context, detail *cycleDetail, run forecastRun) {
	var page productPage
	path := "/api/v1/products?run_id=" + url.QueryEscape(run.RunID) + "&limit=100"
	if err := handler.readCore(ctx, path, &page); err != nil {
		detail.Warnings = append(detail.Warnings, "forecast-products")
		return
	}
	for _, item := range page.Items {
		if item.ProductType != "rain_rate" {
			continue
		}
		var assets []productAsset
		if err := handler.readCore(ctx, "/api/v1/products/"+url.PathEscape(item.ProductID)+"/assets", &assets); err != nil {
			detail.Warnings = append(detail.Warnings, "product-assets:"+item.ProductID)
			continue
		}
		panelID, displayName, lifecycle := deterministicPanelIdentity(item.ModelID)
		panel := panelView{PanelID: panelID, AlgorithmID: item.ModelID, DisplayName: displayName,
			Role: "forecast", Lifecycle: lifecycle, DataKind: "rain_rate", CadenceMinutes: 5,
			Status: "ready", LegendUnit: "mm/h", Legend: rainfallLegend()}
		for _, asset := range assets {
			if asset.AssetType != "rendered_png" || asset.ContentURL == "" {
				continue
			}
			lead := 0
			if asset.LeadMinutes != nil {
				lead = *asset.LeadMinutes
			}
			validTime := asset.ValidTime
			if validTime == "" {
				validTime = addLead(run.IssueTime, lead)
			}
			unit := "mm/h"
			if asset.Unit != nil {
				unit = *asset.Unit
			}
			panel.Frames = append(panel.Frames, frameView{AssetID: asset.AssetID,
				ValidTime: validTime, LeadMinutes: lead, ImageURL: asset.ContentURL,
				MediaType: asset.MediaType, Unit: unit, SHA256: asset.SHA256,
				CoverageRatio: asset.CoverageRatio, ValidCellCount: asset.ValidCellCount,
				MissingCount: asset.MissingCount})
		}
		if len(panel.Frames) == 0 {
			continue
		}
		sortFrames(panel.Frames)
		upsertPanel(detail, panel)
		if panelID == "nowcastnet" {
			detail.Capabilities.NowcastNet = true
		}
	}
}

func deterministicPanelIdentity(modelID string) (string, string, string) {
	normalized := strings.ToLower(modelID)
	switch {
	case strings.Contains(normalized, "nowcastnet"):
		return "nowcastnet", "NowcastNet", "shadow"
	case strings.Contains(normalized, "pysteps") || normalized == "lk" || normalized == "":
		return "lk", "pySTEPS-LK", "shadow"
	default:
		return normalized, modelID, "shadow"
	}
}

func (handler *Handler) addEnsemble(ctx context.Context, detail *cycleDetail, cycle ensembleCycle) {
	query := url.Values{"issue_time": {cycle.IssueTime}, "grid_id": {cycle.GridID}}
	var bundle ensembleBundle
	if err := handler.readCore(ctx, "/api/v1/ensemble-products/by-cycle?"+query.Encode(), &bundle); err != nil {
		detail.Warnings = append(detail.Warnings, "ensemble-product")
		return
	}
	layer := preferredEnsembleLayer(bundle.Layers)
	if layer == nil {
		return
	}
	panel := panelView{PanelID: "steps", AlgorithmID: "pysteps-steps",
		DisplayName: "pySTEPS-STEPS", Role: "forecast", Lifecycle: "offline",
		DataKind: layer.ProductType, CadenceMinutes: 5, Status: "ready"}
	if bundle.OperationalEligible {
		panel.Lifecycle = "operational"
	}
	if layer.Unit != nil {
		panel.LegendUnit = *layer.Unit
	}
	if panel.LegendUnit == "" && layer.ProductType == "probability_exceedance" {
		panel.LegendUnit = "%"
	}
	for _, entry := range layer.Legend {
		value := entry.Minimum
		if layer.ProductType == "probability_exceedance" {
			value *= 100
		}
		panel.Legend = append(panel.Legend, legendEntry{Minimum: &value, Label: entry.Label, Color: entry.Color})
	}
	for _, asset := range layer.Assets {
		if asset.AssetType != "rendered_png" || asset.ContentURL == "" {
			continue
		}
		lead := 0
		if asset.LeadMinutes != nil {
			lead = *asset.LeadMinutes
		}
		unit := panel.LegendUnit
		if asset.Unit != nil {
			unit = *asset.Unit
		}
		panel.Frames = append(panel.Frames, frameView{AssetID: asset.AssetID,
			ValidTime: asset.ValidTime, LeadMinutes: lead, ImageURL: asset.ContentURL,
			MediaType: asset.MediaType, Unit: unit, SHA256: asset.SHA256,
			CoverageRatio: asset.CoverageRatio, ValidCellCount: asset.ValidCellCount,
			MissingCount: asset.MissingCount})
	}
	if len(panel.Frames) == 0 {
		return
	}
	sortFrames(panel.Frames)
	upsertPanel(detail, panel)
}

func preferredEnsembleLayer(layers []ensembleLayer) *ensembleLayer {
	for index := range layers {
		if layers[index].ProductType == "quantile" && layers[index].Quantile != nil &&
			*layers[index].Quantile == 0.5 {
			return &layers[index]
		}
	}
	for index := range layers {
		if layers[index].ProductType == "probability_exceedance" &&
			layers[index].ThresholdMMH != nil && *layers[index].ThresholdMMH == 5 {
			return &layers[index]
		}
	}
	return nil
}

func ensureStablePanels(detail *cycleDetail) {
	stable := []panelView{
		{PanelID: "qpe", AlgorithmID: "radar-analysis", DisplayName: "雷达 QPE",
			Role: "observation", Lifecycle: "analysis", DataKind: "rain_rate",
			CadenceMinutes: 5, Status: "unavailable", UnavailableReason: "radar_qpe_unavailable", Frames: []frameView{}},
		{PanelID: "lk", AlgorithmID: "pysteps-lk", DisplayName: "pySTEPS-LK",
			Role: "forecast", Lifecycle: "shadow", DataKind: "rain_rate",
			CadenceMinutes: 5, Status: "unavailable", UnavailableReason: "lk_product_unavailable", Frames: []frameView{}},
		{PanelID: "steps", AlgorithmID: "pysteps-steps", DisplayName: "pySTEPS-STEPS",
			Role: "forecast", Lifecycle: "offline", DataKind: "quantile",
			CadenceMinutes: 5, Status: "unavailable", UnavailableReason: "steps_product_unavailable", Frames: []frameView{}},
		{PanelID: "nowcastnet", AlgorithmID: "nowcastnet", DisplayName: "NowcastNet",
			Role: "forecast", Lifecycle: "shadow", DataKind: "rain_rate",
			CadenceMinutes: 10, Status: "unavailable",
			UnavailableReason: "shadow_input_or_product_unavailable", Frames: []frameView{}},
	}
	for _, panel := range stable {
		if panelIndex(detail.Panels, panel.PanelID) < 0 {
			detail.Panels = append(detail.Panels, panel)
		}
	}
	sort.SliceStable(detail.Panels, func(left, right int) bool {
		return panelRank(detail.Panels[left].PanelID) < panelRank(detail.Panels[right].PanelID)
	})
}

func (handler *Handler) applyNowcastNetShadowStatus(ctx context.Context, detail *cycleDetail) {
	if handler.nowcastNetStatusURL == "" {
		return
	}
	index := panelIndex(detail.Panels, "nowcastnet")
	if index < 0 || len(detail.Panels[index].Frames) > 0 {
		return
	}
	var status nowcastNetShadowStatus
	if err := handler.readRemoteJSON(ctx, handler.nowcastNetStatusURL, &status); err != nil {
		detail.Warnings = append(detail.Warnings, "nowcastnet-shadow-status")
		return
	}
	if status.IssueTime == "" {
		detail.Panels[index].UnavailableReason = shadowStatusReason(status, "shadow_cycle_not_yet_evaluated")
		return
	}
	statusTime, ok := normalizedTime(status.IssueTime)
	detailTime, detailOK := normalizedTime(detail.IssueTime)
	if !ok || !detailOK || !statusTime.Equal(detailTime) {
		detail.Panels[index].UnavailableReason = "shadow_cycle_not_yet_evaluated"
		return
	}
	detail.Panels[index].AlgorithmID = status.ProfileVersion
	switch status.Status {
	case "starting":
		detail.Panels[index].Status = "running"
		detail.Panels[index].UnavailableReason = "shadow_probe_starting"
	case "input_eligible", "running":
		detail.Panels[index].Status = "running"
		detail.Panels[index].UnavailableReason = shadowStatusReason(status, "shadow_inference_pending")
	case "failed":
		detail.Panels[index].Status = "failed"
		detail.Panels[index].UnavailableReason = "shadow_probe_failed"
	case "waiting", "input_ineligible":
		detail.Panels[index].Status = "unavailable"
		detail.Panels[index].UnavailableReason = shadowStatusReason(status, "shadow_input_ineligible")
	default:
		detail.Panels[index].Status = "unavailable"
		detail.Panels[index].UnavailableReason = shadowStatusReason(status, "shadow_status_unknown")
	}
}

func shadowStatusReason(status nowcastNetShadowStatus, fallback string) string {
	reason := strings.TrimSpace(status.Reason)
	if reason == "" || strings.Contains(reason, ":") {
		return fallback
	}
	return reason
}

func finalizeTimeline(detail *cycleDetail) {
	values := map[string]struct{}{detail.IssueTime: {}}
	for _, panel := range detail.Panels {
		for _, frame := range panel.Frames {
			if parsed, ok := normalizedTime(frame.ValidTime); ok {
				values[parsed.Format(time.RFC3339)] = struct{}{}
			}
		}
	}
	for value := range values {
		detail.Timeline = append(detail.Timeline, value)
	}
	sort.Strings(detail.Timeline)
	detail.Warnings = uniqueStrings(detail.Warnings)
}

func (handler *Handler) readCore(ctx context.Context, path string, target any) error {
	request := httptest.NewRequestWithContext(ctx, http.MethodGet, path, nil)
	recorder := httptest.NewRecorder()
	handler.core.ServeHTTP(recorder, request)
	if recorder.Code < 200 || recorder.Code >= 300 {
		return fmt.Errorf("upstream %s returned %d", path, recorder.Code)
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), target); err != nil {
		return fmt.Errorf("decode upstream %s: %w", path, err)
	}
	return nil
}

func encodeCycleID(gridID string, issueTime time.Time) string {
	return base64.RawURLEncoding.EncodeToString([]byte(cycleKey(gridID, issueTime)))
}

func decodeCycleID(value string) (string, time.Time, error) {
	decoded, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil {
		return "", time.Time{}, errors.New("invalid workspace cycle ID")
	}
	parts := strings.SplitN(string(decoded), "|", 2)
	if len(parts) != 2 || parts[0] == "" {
		return "", time.Time{}, errors.New("invalid workspace cycle identity")
	}
	parsed, err := time.Parse(time.RFC3339, parts[1])
	if err != nil {
		return "", time.Time{}, errors.New("invalid workspace cycle time")
	}
	return parts[0], parsed.UTC(), nil
}

func cycleKey(gridID string, issueTime time.Time) string {
	return gridID + "|" + issueTime.UTC().Format(time.RFC3339)
}

func normalizedTime(value string) (time.Time, bool) {
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return time.Time{}, false
	}
	return parsed.UTC().Truncate(time.Second), true
}

func addLead(issue string, lead int) string {
	parsed, ok := normalizedTime(issue)
	if !ok {
		return issue
	}
	return parsed.Add(time.Duration(lead) * time.Minute).Format(time.RFC3339)
}

func queryLimit(value string, fallback, maximum int) int {
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed < 1 {
		return fallback
	}
	if parsed > maximum {
		return maximum
	}
	return parsed
}

func fourBounds(values []float64) ([4]float64, bool) {
	if len(values) != 4 || values[0] >= values[2] || values[1] >= values[3] {
		return [4]float64{}, false
	}
	return [4]float64{values[0], values[1], values[2], values[3]}, true
}

func upsertPanel(detail *cycleDetail, panel panelView) {
	if index := panelIndex(detail.Panels, panel.PanelID); index >= 0 {
		detail.Panels[index] = panel
		return
	}
	detail.Panels = append(detail.Panels, panel)
}

func panelIndex(panels []panelView, panelID string) int {
	for index := range panels {
		if panels[index].PanelID == panelID {
			return index
		}
	}
	return -1
}

func panelRank(panelID string) int {
	switch panelID {
	case "qpe":
		return 0
	case "lk":
		return 1
	case "steps":
		return 2
	case "nowcastnet":
		return 3
	}
	if strings.HasPrefix(panelID, "dbzh_raw:") {
		return 10
	}
	if strings.HasPrefix(panelID, "dbzh_qc:") {
		return 11
	}
	return 20
}

func sortFrames(frames []frameView) {
	sort.Slice(frames, func(left, right int) bool {
		if frames[left].LeadMinutes == frames[right].LeadMinutes {
			return frames[left].ValidTime < frames[right].ValidTime
		}
		return frames[left].LeadMinutes < frames[right].LeadMinutes
	})
}

func rainfallLegend() []legendEntry {
	values := []struct {
		minimum float64
		color   string
	}{
		{0.1, "#9dd9ff"}, {1, "#4ba3f2"}, {2.5, "#2a79c7"},
		{5, "#3ca85b"}, {10, "#9acb3c"}, {25, "#efd23a"},
		{50, "#ee8a2d"}, {100, "#cf453b"}, {200, "#862f82"},
	}
	result := make([]legendEntry, 0, len(values))
	for _, item := range values {
		minimum := item.minimum
		result = append(result, legendEntry{Minimum: &minimum, Color: item.color})
	}
	return result
}

func uniqueStrings(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	result := make([]string, 0, len(values))
	for _, value := range values {
		if value == "" {
			continue
		}
		if _, duplicate := seen[value]; duplicate {
			continue
		}
		seen[value] = struct{}{}
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json; charset=utf-8")
	response.Header().Set("Cache-Control", "no-store")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(value)
}

func writeError(response http.ResponseWriter, status int, err error) {
	writeJSON(response, status, map[string]string{"error": err.Error()})
}

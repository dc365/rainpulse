package verification

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"sync"
	"time"
)

const maximumSummaryBytes = 1 << 20

const (
	maximumMapManifestBytes = 2 << 20
	maximumMapAssetBytes    = 1 << 20
)

var (
	ErrNotFound      = errors.New("algorithm-verification run not found")
	ErrInvalidReport = errors.New("algorithm-verification report is invalid")
	segmentPattern   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
	issueKeyPattern  = regexp.MustCompile(`^[0-9]{8}T[0-9]{6}Z$`)
)

type RunSummary struct {
	ProfileVersion           string
	RunID                    string
	SchemaVersion            string
	PrimaryTruthKind         string
	OperationalEligible      bool
	CompletedIssueCount      int
	FailedIssueCount         int
	MotionFallbackIssueCount int
	MetricRowCount           int
	SkillStatus              string
	MapsAvailable            bool
	MapBundleCount           int
	MapLayerCount            int
	MapRendererVersion       string
	ModifiedAt               time.Time
}

type SkillComparison struct {
	Baseline                    string             `json:"baseline"`
	BootstrapSampleCount        int                `json:"bootstrap_sample_count"`
	CaseMeanDifferences         map[string]float64 `json:"case_mean_differences"`
	EvaluableCaseCount          int                `json:"evaluable_case_count"`
	MaximumLeadMinutes          int                `json:"maximum_lead_minutes"`
	MeanDifference95pctInterval []*float64         `json:"mean_difference_95pct_interval"`
	MeanFSSDifference           *float64           `json:"mean_fss_difference"`
	PassesCaseGate              bool               `json:"passes_case_gate"`
	PositiveCaseCount           int                `json:"positive_case_count"`
	ThresholdMMH                float64            `json:"threshold_mm_h"`
	TotalWetCaseCount           int                `json:"total_wet_case_count"`
	WindowPixels                int                `json:"window_pixels"`
}

type SkillSummary struct {
	CandidateModel   string            `json:"candidate_model"`
	Status           string            `json:"status"`
	ComparisonMetric string            `json:"comparison_metric"`
	Comparisons      []SkillComparison `json:"comparisons"`
}

type Case struct {
	CaseID     string
	Category   string
	IssueTimes []time.Time
}

type FilterOptions struct {
	Models        []string
	LeadMinutes   []int
	ThresholdsMMH []float64
	WindowsPixels []int
	FSSScales     []FSSScale
}

type FSSScale struct {
	WindowPixels int
	TargetKM     float64
	ActualKMMin  float64
	ActualKMMax  float64
}

type RunDetail struct {
	Run          RunSummary
	Cases        []Case
	Filters      FilterOptions
	SkillSummary SkillSummary
}

type Metric struct {
	CaseID                          string
	CaseCategory                    string
	IssueTime                       time.Time
	TruthKind                       string
	Model                           string
	LeadMinutes                     int
	ThresholdMMH                    float64
	WindowPixels                    int
	WindowKM                        float64
	WindowTargetKM                  float64
	Hits                            int64
	Misses                          int64
	FalseAlarms                     int64
	CorrectNegatives                int64
	CSI                             *float64
	POD                             *float64
	FAR                             *float64
	FSS                             *float64
	MAEMMH                          *float64
	RMSEMMH                         *float64
	MeanErrorMMH                    *float64
	TruthCoverage                   *float64
	ForecastCoverage                *float64
	CommonCoverage                  *float64
	ForecastToTruthCoverage         *float64
	AdvectionDomainToTruthCoverage  *float64
	AdvectionBoundaryLossRatio      *float64
	InteriorMissingLossRatio        *float64
	BoundaryAdjustedCoverage        *float64
	CoverageDecompositionClosureErr *float64
}

type MetricFilter struct {
	CaseID       string
	IssueTime    time.Time
	ThresholdMMH float64
	WindowPixels int
}

type MapFrameFilter struct {
	CaseID      string
	IssueTime   time.Time
	LeadMinutes int
}

type MapMotionVector struct {
	Longitude      float64 `json:"longitude"`
	Latitude       float64 `json:"latitude"`
	EndLongitude   float64 `json:"end_longitude"`
	EndLatitude    float64 `json:"end_latitude"`
	UPixelsPerStep float64 `json:"u_pixels_per_step"`
	VPixelsPerStep float64 `json:"v_pixels_per_step"`
}

type MapMotion struct {
	FallbackUsed            bool              `json:"fallback_used"`
	FallbackReason          *string           `json:"fallback_reason"`
	FeatureCount            int               `json:"feature_count"`
	TrackableRainPixelCount int               `json:"trackable_rain_pixel_count"`
	Unit                    string            `json:"unit"`
	Vectors                 []MapMotionVector `json:"vectors"`
}

type MapLayer struct {
	AssetID          string    `json:"asset_id"`
	Role             string    `json:"role"`
	Model            *string   `json:"model"`
	LeadMinutes      int       `json:"lead_minutes"`
	ValidTime        time.Time `json:"-"`
	ValidTimeText    string    `json:"valid_time_utc"`
	ObjectPath       string    `json:"object_path"`
	MediaType        string    `json:"media_type"`
	SHA256           string    `json:"sha256"`
	SizeBytes        int64     `json:"size_bytes"`
	Width            int       `json:"width"`
	Height           int       `json:"height"`
	ValidCellCount   int64     `json:"valid_cell_count"`
	NoRainCellCount  int64     `json:"no_rain_cell_count"`
	RainCellCount    int64     `json:"rain_cell_count"`
	MissingCellCount int64     `json:"missing_cell_count"`
}

type MapFrame struct {
	ContractVersion     string
	RendererVersion     string
	PaletteVersion      string
	ProfileVersion      string
	RunID               string
	CaseID              string
	IssueTime           time.Time
	ValidTime           time.Time
	LeadMinutes         int
	TruthKind           string
	OperationalEligible bool
	Projection          string
	PixelEdgeBounds     []float64
	FitBounds           []float64
	Width               int
	Height              int
	RainThresholdMMH    float64
	ValidNoRainColor    string
	Legend              []MapLegendEntry
	Layers              []MapLayer
	Motion              MapMotion
}

type MapLegendEntry struct {
	MinimumMMH float64 `json:"minimum"`
	Color      string  `json:"color"`
}

type MapAssetContent struct {
	Data   []byte
	SHA256 string
}

type reportSummary struct {
	SchemaVersion             string       `json:"schema_version"`
	ProfileVersion            string       `json:"profile_version"`
	PrimaryTruthKind          string       `json:"primary_truth_kind"`
	OperationalEligible       bool         `json:"operational_eligible"`
	CompletedIssueCount       int          `json:"completed_issue_count"`
	FailedIssueCount          int          `json:"failed_issue_count"`
	MotionFallbackIssueCount  int          `json:"motion_fallback_issue_count"`
	MetricRowCount            int          `json:"metric_row_count"`
	TruthDomainMetricRowCount int          `json:"truth_domain_metric_row_count"`
	MapBundleCount            int          `json:"map_bundle_count"`
	MapLayerCount             int          `json:"map_layer_count"`
	MapRendererVersion        string       `json:"map_renderer_version"`
	Errors                    []any        `json:"errors"`
	SkillSummary              SkillSummary `json:"skill_summary"`
	ReportFiles               reportFiles  `json:"report_files"`
}

type reportFiles struct {
	FixedTruthDomain string `json:"fixed_truth_domain"`
}

type mapManifest struct {
	ContractVersion            string     `json:"contract_version"`
	RendererVersion            string     `json:"renderer_version"`
	PaletteVersion             string     `json:"palette_version"`
	VerificationProfileVersion string     `json:"verification_profile_version"`
	CaseID                     string     `json:"case_id"`
	IssueKey                   string     `json:"issue_key"`
	IssueTimeText              string     `json:"issue_time_utc"`
	TruthKind                  string     `json:"truth_kind"`
	OperationalEligible        bool       `json:"operational_eligible"`
	Grid                       mapGrid    `json:"grid"`
	Palette                    mapPalette `json:"palette"`
	Motion                     MapMotion  `json:"motion"`
	LeadMinutes                []int      `json:"lead_minutes"`
	Layers                     []MapLayer `json:"layers"`
}

type mapIndex struct {
	ContractVersion            string `json:"contract_version"`
	VerificationProfileVersion string `json:"verification_profile_version"`
	RendererVersion            string `json:"renderer_version"`
	BundleCount                int    `json:"bundle_count"`
	LayerCount                 int    `json:"layer_count"`
}

type mapGrid struct {
	GridID            string    `json:"grid_id"`
	GridConfigVersion string    `json:"grid_config_version"`
	Projection        string    `json:"projection"`
	FitBounds         []float64 `json:"fit_bounds"`
	PixelEdgeBounds   []float64 `json:"pixel_edge_bounds"`
	Width             int       `json:"width"`
	Height            int       `json:"height"`
}

type mapPalette struct {
	RainThresholdMMH float64          `json:"rain_threshold_mm_h"`
	ValidNoRainColor string           `json:"valid_no_rain_color"`
	Stops            []MapLegendEntry `json:"stops"`
}

type fileStamp struct {
	Size    int64
	ModTime time.Time
}

type cachedRun struct {
	SummaryStamp    fileStamp
	MetricsStamp    fileStamp
	Detail          RunDetail
	Metrics         []Metric
	UsesTruthDomain bool
}

type FileStore struct {
	root  string
	mu    sync.RWMutex
	cache map[string]cachedRun
}

func NewFileStore(root string) *FileStore {
	return &FileStore{
		root:  filepath.Clean(root),
		cache: make(map[string]cachedRun),
	}
}

func (store *FileStore) ListRuns(ctx context.Context) ([]RunSummary, error) {
	profiles, err := os.ReadDir(store.root)
	if errors.Is(err, os.ErrNotExist) {
		return []RunSummary{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("list algorithm-verification profiles: %w", err)
	}
	runs := make([]RunSummary, 0)
	for _, profile := range profiles {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !profile.IsDir() || !validSegment(profile.Name()) {
			continue
		}
		runEntries, err := os.ReadDir(filepath.Join(store.root, profile.Name()))
		if err != nil {
			return nil, fmt.Errorf("list runs for profile %s: %w", profile.Name(), err)
		}
		for _, runEntry := range runEntries {
			if !runEntry.IsDir() || !validSegment(runEntry.Name()) {
				continue
			}
			summaryPath := filepath.Join(store.root, profile.Name(), runEntry.Name(), "summary.json")
			if _, err := os.Stat(summaryPath); errors.Is(err, os.ErrNotExist) {
				continue
			} else if err != nil {
				return nil, fmt.Errorf("stat summary for %s/%s: %w", profile.Name(), runEntry.Name(), err)
			}
			summary, _, err := store.readSummary(profile.Name(), runEntry.Name())
			if err != nil {
				return nil, err
			}
			runs = append(runs, summary)
		}
	}
	sort.Slice(runs, func(i, j int) bool {
		if runs[i].CompletedIssueCount != runs[j].CompletedIssueCount {
			return runs[i].CompletedIssueCount > runs[j].CompletedIssueCount
		}
		return runs[i].ModifiedAt.After(runs[j].ModifiedAt)
	})
	return runs, nil
}

func (store *FileStore) GetRun(
	ctx context.Context,
	profileVersion string,
	runID string,
) (RunDetail, error) {
	loaded, err := store.loadRun(ctx, profileVersion, runID)
	if err != nil {
		return RunDetail{}, err
	}
	return loaded.Detail, nil
}

func (store *FileStore) ListMetrics(
	ctx context.Context,
	profileVersion string,
	runID string,
	filter MetricFilter,
) ([]Metric, error) {
	if !validSegment(filter.CaseID) || filter.WindowPixels < 1 || filter.ThresholdMMH < 0 ||
		filter.IssueTime.IsZero() {
		return nil, fmt.Errorf("%w: metric filter is invalid", ErrInvalidReport)
	}
	loaded, err := store.loadRun(ctx, profileVersion, runID)
	if err != nil {
		return nil, err
	}
	items := make([]Metric, 0, 36)
	issueTime := filter.IssueTime.UTC()
	targetKM := 0.0
	if loaded.UsesTruthDomain {
		found := false
		for _, scale := range loaded.Detail.Filters.FSSScales {
			if scale.WindowPixels == filter.WindowPixels {
				targetKM = scale.TargetKM
				found = true
				break
			}
		}
		if !found {
			return nil, ErrNotFound
		}
	}
	for index, metric := range loaded.Metrics {
		if index%1024 == 0 {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
		}
		windowMatches := metric.WindowPixels == filter.WindowPixels
		if loaded.UsesTruthDomain {
			windowMatches = math.Abs(metric.WindowTargetKM-targetKM) < 1e-6
		}
		if metric.CaseID == filter.CaseID && metric.IssueTime.Equal(issueTime) &&
			windowMatches &&
			math.Abs(metric.ThresholdMMH-filter.ThresholdMMH) < 1e-9 {
			items = append(items, metric)
		}
	}
	if len(items) == 0 {
		return nil, ErrNotFound
	}
	if len(items) > 512 {
		return nil, fmt.Errorf("%w: metric filter exceeds response limit", ErrInvalidReport)
	}
	return items, nil
}

func (store *FileStore) GetMapFrame(
	ctx context.Context,
	profileVersion string,
	runID string,
	filter MapFrameFilter,
) (MapFrame, error) {
	if !validSegment(filter.CaseID) || filter.IssueTime.IsZero() || filter.LeadMinutes < 1 {
		return MapFrame{}, fmt.Errorf("%w: map-frame filter is invalid", ErrInvalidReport)
	}
	if err := ctx.Err(); err != nil {
		return MapFrame{}, err
	}
	issueTime := filter.IssueTime.UTC()
	issueKey := issueTime.Format("20060102T150405Z")
	manifest, _, err := store.readMapManifest(profileVersion, runID, filter.CaseID, issueKey)
	if err != nil {
		return MapFrame{}, err
	}
	if manifest.IssueTimeText != issueTime.Format(time.RFC3339) {
		return MapFrame{}, fmt.Errorf("%w: map issue time differs from request", ErrInvalidReport)
	}
	issueParsed, err := time.Parse(time.RFC3339, manifest.IssueTimeText)
	if err != nil {
		return MapFrame{}, fmt.Errorf("%w: map issue time is invalid", ErrInvalidReport)
	}
	layers := make([]MapLayer, 0, 4)
	var validTime time.Time
	for _, layer := range manifest.Layers {
		if layer.LeadMinutes != filter.LeadMinutes {
			continue
		}
		parsed, err := time.Parse(time.RFC3339, layer.ValidTimeText)
		if err != nil {
			return MapFrame{}, fmt.Errorf("%w: map valid time is invalid", ErrInvalidReport)
		}
		layer.ValidTime = parsed.UTC()
		if validTime.IsZero() {
			validTime = layer.ValidTime
		} else if !validTime.Equal(layer.ValidTime) {
			return MapFrame{}, fmt.Errorf("%w: map layer valid times differ", ErrInvalidReport)
		}
		layers = append(layers, layer)
	}
	if len(layers) < 2 || validTime.IsZero() {
		return MapFrame{}, ErrNotFound
	}
	return MapFrame{
		ContractVersion: manifest.ContractVersion, RendererVersion: manifest.RendererVersion,
		PaletteVersion: manifest.PaletteVersion, ProfileVersion: profileVersion, RunID: runID,
		CaseID: filter.CaseID, IssueTime: issueParsed.UTC(), ValidTime: validTime,
		LeadMinutes: filter.LeadMinutes, TruthKind: manifest.TruthKind,
		OperationalEligible: manifest.OperationalEligible, Projection: manifest.Grid.Projection,
		PixelEdgeBounds: manifest.Grid.PixelEdgeBounds, FitBounds: manifest.Grid.FitBounds,
		Width: manifest.Grid.Width, Height: manifest.Grid.Height,
		RainThresholdMMH: manifest.Palette.RainThresholdMMH,
		ValidNoRainColor: manifest.Palette.ValidNoRainColor,
		Legend:           manifest.Palette.Stops,
		Layers:           layers, Motion: manifest.Motion,
	}, nil
}

func (store *FileStore) ReadMapAsset(
	ctx context.Context,
	profileVersion string,
	runID string,
	caseID string,
	issueKey string,
	assetID string,
) (MapAssetContent, error) {
	if err := ctx.Err(); err != nil {
		return MapAssetContent{}, err
	}
	if !validSegment(assetID) {
		return MapAssetContent{}, ErrNotFound
	}
	manifest, directory, err := store.readMapManifest(profileVersion, runID, caseID, issueKey)
	if err != nil {
		return MapAssetContent{}, err
	}
	var selected *MapLayer
	for index := range manifest.Layers {
		if manifest.Layers[index].AssetID == assetID {
			selected = &manifest.Layers[index]
			break
		}
	}
	if selected == nil {
		return MapAssetContent{}, ErrNotFound
	}
	path, err := safeMapAssetPath(directory, selected.ObjectPath)
	if err != nil {
		return MapAssetContent{}, err
	}
	info, err := os.Stat(path)
	if err != nil {
		return MapAssetContent{}, reportFileError(err)
	}
	if !info.Mode().IsRegular() || info.Size() < 24 || info.Size() > maximumMapAssetBytes ||
		info.Size() != selected.SizeBytes {
		return MapAssetContent{}, fmt.Errorf("%w: map asset size is invalid", ErrInvalidReport)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return MapAssetContent{}, reportFileError(err)
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(data))
	if digest != selected.SHA256 || !validPNG(data, selected.Width, selected.Height) {
		return MapAssetContent{}, fmt.Errorf("%w: map asset failed integrity validation", ErrInvalidReport)
	}
	return MapAssetContent{Data: data, SHA256: digest}, nil
}

func (store *FileStore) readMapManifest(
	profileVersion string,
	runID string,
	caseID string,
	issueKey string,
) (mapManifest, string, error) {
	if !validSegment(caseID) || !issueKeyPattern.MatchString(issueKey) {
		return mapManifest{}, "", ErrNotFound
	}
	runDirectory, err := store.runDirectory(profileVersion, runID)
	if err != nil {
		return mapManifest{}, "", err
	}
	directory := filepath.Join(runDirectory, "maps", caseID, issueKey)
	path := filepath.Join(directory, "manifest.json")
	info, err := os.Stat(path)
	if err != nil {
		return mapManifest{}, "", reportFileError(err)
	}
	if !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > maximumMapManifestBytes {
		return mapManifest{}, "", fmt.Errorf("%w: map manifest size is invalid", ErrInvalidReport)
	}
	handle, err := os.Open(path)
	if err != nil {
		return mapManifest{}, "", reportFileError(err)
	}
	defer handle.Close()
	var manifest mapManifest
	if err := json.NewDecoder(io.LimitReader(handle, maximumMapManifestBytes+1)).Decode(&manifest); err != nil {
		return mapManifest{}, "", fmt.Errorf("%w: decode map manifest: %v", ErrInvalidReport, err)
	}
	if err := validateMapManifest(manifest, profileVersion, caseID, issueKey); err != nil {
		return mapManifest{}, "", err
	}
	return manifest, directory, nil
}

func (store *FileStore) loadRun(
	ctx context.Context,
	profileVersion string,
	runID string,
) (cachedRun, error) {
	directory, err := store.runDirectory(profileVersion, runID)
	if err != nil {
		return cachedRun{}, err
	}
	summaryStamp, err := statFile(filepath.Join(directory, "summary.json"))
	if err != nil {
		return cachedRun{}, reportFileError(err)
	}
	summary, report, err := store.readSummary(profileVersion, runID)
	if err != nil {
		return cachedRun{}, err
	}
	metricsFilename, expectedMetricCount, usesTruthDomain, err := report.displayMetricSource()
	if err != nil {
		return cachedRun{}, err
	}
	metricsPath := filepath.Join(directory, metricsFilename)
	metricsStamp, err := statFile(metricsPath)
	if err != nil {
		return cachedRun{}, reportFileError(err)
	}
	cacheKey := profileVersion + "/" + runID
	store.mu.RLock()
	cached, ok := store.cache[cacheKey]
	store.mu.RUnlock()
	if ok && cached.SummaryStamp == summaryStamp && cached.MetricsStamp == metricsStamp {
		return cached, nil
	}

	metrics, cases, filters, err := readMetricsCSV(ctx, metricsPath)
	if err != nil {
		return cachedRun{}, err
	}
	if len(metrics) != expectedMetricCount {
		return cachedRun{}, fmt.Errorf(
			"%w: summary display metric count=%d differs from CSV rows=%d",
			ErrInvalidReport,
			expectedMetricCount,
			len(metrics),
		)
	}
	if usesTruthDomain {
		filters = consolidateTargetFSSScales(filters)
		filters.Models, err = report.displayModels(filters.Models)
		if err != nil {
			return cachedRun{}, err
		}
	}
	loaded := cachedRun{
		SummaryStamp: summaryStamp,
		MetricsStamp: metricsStamp,
		Detail: RunDetail{
			Run: summary, Cases: cases, Filters: filters,
			SkillSummary: report.SkillSummary,
		},
		Metrics: metrics, UsesTruthDomain: usesTruthDomain,
	}
	store.mu.Lock()
	store.cache[cacheKey] = loaded
	store.mu.Unlock()
	return loaded, nil
}

func consolidateTargetFSSScales(filters FilterOptions) FilterOptions {
	scales := make([]FSSScale, 0, len(filters.FSSScales))
	for _, candidate := range filters.FSSScales {
		matched := false
		for index := range scales {
			if math.Abs(scales[index].TargetKM-candidate.TargetKM) >= 1e-6 {
				continue
			}
			scales[index].ActualKMMin = math.Min(scales[index].ActualKMMin, candidate.ActualKMMin)
			scales[index].ActualKMMax = math.Max(scales[index].ActualKMMax, candidate.ActualKMMax)
			matched = true
			break
		}
		if !matched {
			scales = append(scales, candidate)
		}
	}
	sort.Slice(scales, func(i, j int) bool {
		return scales[i].TargetKM < scales[j].TargetKM
	})
	selectors := make([]int, 0, len(scales))
	for _, scale := range scales {
		selectors = append(selectors, scale.WindowPixels)
	}
	filters.FSSScales = scales
	filters.WindowsPixels = selectors
	return filters
}

func (report reportSummary) displayMetricSource() (string, int, bool, error) {
	if report.ReportFiles.FixedTruthDomain == "" {
		return "metrics.csv", report.MetricRowCount, false, nil
	}
	if report.ReportFiles.FixedTruthDomain != "metrics_truth_domain.csv" ||
		report.TruthDomainMetricRowCount < 1 {
		return "", 0, false, fmt.Errorf(
			"%w: fixed truth-domain metric source is invalid",
			ErrInvalidReport,
		)
	}
	return report.ReportFiles.FixedTruthDomain, report.TruthDomainMetricRowCount, true, nil
}

func (report reportSummary) displayModels(available []string) ([]string, error) {
	candidate := report.SkillSummary.CandidateModel
	if candidate == "" {
		candidate = "lk"
	}
	wanted := []string{candidate}
	seen := map[string]bool{candidate: true}
	for _, comparison := range report.SkillSummary.Comparisons {
		if comparison.Baseline != "" && !seen[comparison.Baseline] {
			seen[comparison.Baseline] = true
			wanted = append(wanted, comparison.Baseline)
		}
	}
	availableSet := make(map[string]bool, len(available))
	for _, model := range available {
		availableSet[model] = true
	}
	for _, model := range wanted {
		if !availableSet[model] {
			return nil, fmt.Errorf(
				"%w: display model %s is absent from fixed truth-domain metrics",
				ErrInvalidReport,
				model,
			)
		}
	}
	if len(wanted) < 2 {
		return nil, fmt.Errorf("%w: fixed truth-domain report has no comparison baseline", ErrInvalidReport)
	}
	return wanted, nil
}

func (store *FileStore) readSummary(
	profileVersion string,
	runID string,
) (RunSummary, reportSummary, error) {
	directory, err := store.runDirectory(profileVersion, runID)
	if err != nil {
		return RunSummary{}, reportSummary{}, err
	}
	path := filepath.Join(directory, "summary.json")
	info, err := os.Stat(path)
	if err != nil {
		return RunSummary{}, reportSummary{}, reportFileError(err)
	}
	if info.Size() < 1 || info.Size() > maximumSummaryBytes {
		return RunSummary{}, reportSummary{}, fmt.Errorf("%w: summary size is invalid", ErrInvalidReport)
	}
	handle, err := os.Open(path)
	if err != nil {
		return RunSummary{}, reportSummary{}, reportFileError(err)
	}
	defer handle.Close()
	decoder := json.NewDecoder(io.LimitReader(handle, maximumSummaryBytes+1))
	var report reportSummary
	if err := decoder.Decode(&report); err != nil {
		return RunSummary{}, reportSummary{}, fmt.Errorf("%w: decode summary: %v", ErrInvalidReport, err)
	}
	if report.ProfileVersion != profileVersion || report.SchemaVersion == "" ||
		report.PrimaryTruthKind == "" || report.SkillSummary.Status == "" ||
		report.CompletedIssueCount < 0 || report.FailedIssueCount < 0 ||
		report.MotionFallbackIssueCount < 0 || report.MetricRowCount < 0 ||
		report.TruthDomainMetricRowCount < 0 ||
		report.MapBundleCount < 0 || report.MapLayerCount < 0 {
		return RunSummary{}, reportSummary{}, fmt.Errorf("%w: summary identity or counts differ", ErrInvalidReport)
	}
	if _, _, _, err := report.displayMetricSource(); err != nil {
		return RunSummary{}, reportSummary{}, err
	}
	mapsAvailable := false
	if report.MapBundleCount == 0 {
		if report.MapLayerCount != 0 {
			return RunSummary{}, reportSummary{}, fmt.Errorf("%w: map summary counts differ", ErrInvalidReport)
		}
	} else {
		if report.MapRendererVersion == "" || report.MapLayerCount < report.MapBundleCount*2 {
			return RunSummary{}, reportSummary{}, fmt.Errorf("%w: map summary identity is invalid", ErrInvalidReport)
		}
		index, err := readMapIndex(filepath.Join(directory, "maps", "index.json"))
		if err != nil {
			return RunSummary{}, reportSummary{}, err
		}
		if index.ContractVersion != "1.0" || index.VerificationProfileVersion != profileVersion ||
			index.RendererVersion != report.MapRendererVersion ||
			index.BundleCount != report.MapBundleCount || index.LayerCount != report.MapLayerCount {
			return RunSummary{}, reportSummary{}, fmt.Errorf("%w: map index differs from summary", ErrInvalidReport)
		}
		mapsAvailable = true
	}
	return RunSummary{
		ProfileVersion:           report.ProfileVersion,
		RunID:                    runID,
		SchemaVersion:            report.SchemaVersion,
		PrimaryTruthKind:         report.PrimaryTruthKind,
		OperationalEligible:      report.OperationalEligible,
		CompletedIssueCount:      report.CompletedIssueCount,
		FailedIssueCount:         report.FailedIssueCount,
		MotionFallbackIssueCount: report.MotionFallbackIssueCount,
		MetricRowCount:           report.MetricRowCount,
		SkillStatus:              report.SkillSummary.Status,
		MapsAvailable:            mapsAvailable,
		MapBundleCount:           report.MapBundleCount,
		MapLayerCount:            report.MapLayerCount,
		MapRendererVersion:       report.MapRendererVersion,
		ModifiedAt:               info.ModTime().UTC(),
	}, report, nil
}

func readMapIndex(path string) (mapIndex, error) {
	info, err := os.Stat(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return mapIndex{}, fmt.Errorf("%w: map index is missing", ErrInvalidReport)
		}
		return mapIndex{}, reportFileError(err)
	}
	if !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > maximumSummaryBytes {
		return mapIndex{}, fmt.Errorf("%w: map index size is invalid", ErrInvalidReport)
	}
	handle, err := os.Open(path)
	if err != nil {
		return mapIndex{}, reportFileError(err)
	}
	defer handle.Close()
	var index mapIndex
	if err := json.NewDecoder(io.LimitReader(handle, maximumSummaryBytes+1)).Decode(&index); err != nil {
		return mapIndex{}, fmt.Errorf("%w: decode map index: %v", ErrInvalidReport, err)
	}
	return index, nil
}

func validateMapManifest(
	manifest mapManifest,
	profileVersion string,
	caseID string,
	issueKey string,
) error {
	if manifest.ContractVersion != "1.0" || manifest.RendererVersion == "" ||
		manifest.PaletteVersion == "" || manifest.VerificationProfileVersion != profileVersion ||
		manifest.CaseID != caseID || manifest.IssueKey != issueKey || manifest.TruthKind == "" ||
		manifest.OperationalEligible || manifest.Grid.Projection != "EPSG:4326" ||
		manifest.Grid.Width < 1 || manifest.Grid.Height < 1 || len(manifest.Grid.FitBounds) != 4 ||
		len(manifest.Grid.PixelEdgeBounds) != 4 || manifest.Palette.RainThresholdMMH < 0 ||
		manifest.Palette.ValidNoRainColor == "" || len(manifest.Palette.Stops) < 1 ||
		len(manifest.Palette.Stops) > 32 || len(manifest.Layers) < 2 ||
		len(manifest.Motion.Vectors) > 200 || manifest.Motion.FeatureCount < 0 ||
		manifest.Motion.TrackableRainPixelCount < 0 || manifest.Motion.Unit == "" {
		return fmt.Errorf("%w: map manifest identity or dimensions are invalid", ErrInvalidReport)
	}
	if _, err := time.Parse(time.RFC3339, manifest.IssueTimeText); err != nil {
		return fmt.Errorf("%w: map manifest issue time is invalid", ErrInvalidReport)
	}
	seen := make(map[string]bool, len(manifest.Layers))
	for _, layer := range manifest.Layers {
		if !validSegment(layer.AssetID) || seen[layer.AssetID] ||
			(layer.Role != "truth" && layer.Role != "forecast") || layer.LeadMinutes < 1 ||
			layer.MediaType != "image/png" || len(layer.SHA256) != 64 || layer.SizeBytes < 24 ||
			layer.SizeBytes > maximumMapAssetBytes || layer.Width != manifest.Grid.Width ||
			layer.Height != manifest.Grid.Height || layer.ValidCellCount < 0 ||
			layer.NoRainCellCount < 0 || layer.RainCellCount < 0 || layer.MissingCellCount < 0 ||
			layer.ValidCellCount+layer.MissingCellCount != int64(layer.Width*layer.Height) ||
			layer.NoRainCellCount+layer.RainCellCount != layer.ValidCellCount {
			return fmt.Errorf("%w: map layer metadata is invalid", ErrInvalidReport)
		}
		if _, err := safeMapAssetPath("/verification-map", layer.ObjectPath); err != nil {
			return err
		}
		if _, err := time.Parse(time.RFC3339, layer.ValidTimeText); err != nil {
			return fmt.Errorf("%w: map layer valid time is invalid", ErrInvalidReport)
		}
		seen[layer.AssetID] = true
	}
	return nil
}

func safeMapAssetPath(directory string, objectPath string) (string, error) {
	if filepath.IsAbs(objectPath) {
		return "", fmt.Errorf("%w: map asset path is unsafe", ErrInvalidReport)
	}
	clean := filepath.Clean(filepath.FromSlash(objectPath))
	if clean == "." || clean == ".." || filepath.Dir(clean) != "layers" {
		return "", fmt.Errorf("%w: map asset path is unsafe", ErrInvalidReport)
	}
	path := filepath.Join(directory, clean)
	relative, err := filepath.Rel(directory, path)
	if err != nil || relative == ".." || filepath.IsAbs(relative) {
		return "", fmt.Errorf("%w: map asset path is unsafe", ErrInvalidReport)
	}
	return path, nil
}

func validPNG(data []byte, width int, height int) bool {
	return len(data) >= 24 && string(data[:8]) == "\x89PNG\r\n\x1a\n" &&
		string(data[12:16]) == "IHDR" && int(binary.BigEndian.Uint32(data[16:20])) == width &&
		int(binary.BigEndian.Uint32(data[20:24])) == height
}

func (store *FileStore) runDirectory(profileVersion string, runID string) (string, error) {
	if !validSegment(profileVersion) || !validSegment(runID) {
		return "", ErrNotFound
	}
	directory := filepath.Join(store.root, profileVersion, runID)
	relative, err := filepath.Rel(store.root, directory)
	if err != nil || relative == "." || filepath.IsAbs(relative) || relative == ".." {
		return "", ErrNotFound
	}
	return directory, nil
}

func readMetricsCSV(
	ctx context.Context,
	path string,
) ([]Metric, []Case, FilterOptions, error) {
	handle, err := os.Open(path)
	if err != nil {
		return nil, nil, FilterOptions{}, reportFileError(err)
	}
	defer handle.Close()
	reader := csv.NewReader(handle)
	header, err := reader.Read()
	if err != nil {
		return nil, nil, FilterOptions{}, fmt.Errorf("%w: read metrics header: %v", ErrInvalidReport, err)
	}
	columns := make(map[string]int, len(header))
	for index, name := range header {
		columns[name] = index
	}
	for _, required := range metricColumns {
		if _, ok := columns[required]; !ok {
			return nil, nil, FilterOptions{}, fmt.Errorf("%w: metrics column %s is missing", ErrInvalidReport, required)
		}
	}

	metrics := make([]Metric, 0, 4096)
	cases := make([]Case, 0)
	caseIndexes := make(map[string]int)
	issueSeen := make(map[string]bool)
	models := make([]string, 0, 3)
	modelSeen := make(map[string]bool)
	leads := make(map[int]bool)
	thresholds := make(map[float64]bool)
	windows := make(map[int]bool)
	fssScales := make(map[int]FSSScale)
	for rowNumber := 2; ; rowNumber++ {
		if rowNumber%1024 == 0 {
			if err := ctx.Err(); err != nil {
				return nil, nil, FilterOptions{}, err
			}
		}
		row, err := reader.Read()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, nil, FilterOptions{}, fmt.Errorf("%w: read metrics row %d: %v", ErrInvalidReport, rowNumber, err)
		}
		metric, err := parseMetric(row, columns)
		if err != nil {
			return nil, nil, FilterOptions{}, fmt.Errorf("%w: metrics row %d: %v", ErrInvalidReport, rowNumber, err)
		}
		metrics = append(metrics, metric)
		caseIndex, exists := caseIndexes[metric.CaseID]
		if !exists {
			caseIndex = len(cases)
			caseIndexes[metric.CaseID] = caseIndex
			cases = append(cases, Case{CaseID: metric.CaseID, Category: metric.CaseCategory})
		}
		issueKey := metric.CaseID + "\x00" + metric.IssueTime.Format(time.RFC3339Nano)
		if !issueSeen[issueKey] {
			issueSeen[issueKey] = true
			cases[caseIndex].IssueTimes = append(cases[caseIndex].IssueTimes, metric.IssueTime)
		}
		if !modelSeen[metric.Model] {
			modelSeen[metric.Model] = true
			models = append(models, metric.Model)
		}
		leads[metric.LeadMinutes] = true
		thresholds[metric.ThresholdMMH] = true
		windows[metric.WindowPixels] = true
		candidate := FSSScale{
			WindowPixels: metric.WindowPixels,
			TargetKM:     metric.WindowTargetKM,
			ActualKMMin:  metric.WindowKM,
			ActualKMMax:  metric.WindowKM,
		}
		if current, exists := fssScales[metric.WindowPixels]; exists {
			if math.Abs(current.TargetKM-candidate.TargetKM) > 1e-6 {
				return nil, nil, FilterOptions{}, fmt.Errorf(
					"%w: FSS window %d has inconsistent target scales",
					ErrInvalidReport,
					metric.WindowPixels,
				)
			}
			current.ActualKMMin = math.Min(current.ActualKMMin, candidate.ActualKMMin)
			current.ActualKMMax = math.Max(current.ActualKMMax, candidate.ActualKMMax)
			fssScales[metric.WindowPixels] = current
		} else {
			fssScales[metric.WindowPixels] = candidate
		}
	}
	if len(metrics) == 0 {
		return nil, nil, FilterOptions{}, fmt.Errorf("%w: metrics CSV is empty", ErrInvalidReport)
	}
	filters := FilterOptions{
		Models:        models,
		LeadMinutes:   sortedIntKeys(leads),
		ThresholdsMMH: sortedFloatKeys(thresholds),
		WindowsPixels: sortedIntKeys(windows),
		FSSScales:     sortedFSSScales(fssScales),
	}
	return metrics, cases, filters, nil
}

var metricColumns = []string{
	"model", "lead_minutes", "threshold_mm_h", "window_pixels", "window_km",
	"hits", "misses", "false_alarms", "correct_negatives", "csi", "pod", "far",
	"fss", "mae_mm_h", "rmse_mm_h", "mean_error_mm_h", "truth_coverage",
	"forecast_coverage", "common_coverage", "case_id", "case_category",
	"issue_time_utc", "truth_kind",
}

func parseMetric(row []string, columns map[string]int) (Metric, error) {
	value := func(name string) (string, error) {
		index := columns[name]
		if index >= len(row) {
			return "", fmt.Errorf("column %s is absent from row", name)
		}
		return row[index], nil
	}
	optionalValue := func(name string) (string, error) {
		index, exists := columns[name]
		if !exists {
			return "", nil
		}
		if index >= len(row) {
			return "", fmt.Errorf("column %s is absent from row", name)
		}
		return row[index], nil
	}
	stringValue := func(name string) (string, error) {
		result, err := value(name)
		if err != nil || result == "" {
			return "", fmt.Errorf("column %s is empty", name)
		}
		return result, nil
	}
	model, err := stringValue("model")
	if err != nil {
		return Metric{}, err
	}
	caseID, err := stringValue("case_id")
	if err != nil || !validSegment(caseID) {
		return Metric{}, fmt.Errorf("case_id is invalid")
	}
	category, err := stringValue("case_category")
	if err != nil {
		return Metric{}, err
	}
	truthKind, err := stringValue("truth_kind")
	if err != nil {
		return Metric{}, err
	}
	issueText, err := stringValue("issue_time_utc")
	if err != nil {
		return Metric{}, err
	}
	issueTime, err := time.Parse(time.RFC3339, issueText)
	if err != nil {
		return Metric{}, fmt.Errorf("issue_time_utc is invalid: %w", err)
	}
	lead, err := parseInt(value, "lead_minutes")
	if err != nil {
		return Metric{}, err
	}
	threshold, err := parseFloat(value, "threshold_mm_h")
	if err != nil {
		return Metric{}, err
	}
	window, err := parseInt(value, "window_pixels")
	if err != nil {
		return Metric{}, err
	}
	windowKM, err := parseFloat(value, "window_km")
	if err != nil {
		return Metric{}, err
	}
	windowTargetKM := legacyFSSTargetKM(window, windowKM)
	if _, exists := columns["window_target_km"]; exists {
		windowTargetKM, err = parseFloat(value, "window_target_km")
		if err != nil {
			return Metric{}, err
		}
		if math.Abs(windowTargetKM-windowKM) <= 1e-6 {
			windowTargetKM = legacyFSSTargetKM(window, windowKM)
		}
	}
	hits, err := parseInt64(value, "hits")
	if err != nil {
		return Metric{}, err
	}
	misses, err := parseInt64(value, "misses")
	if err != nil {
		return Metric{}, err
	}
	falseAlarms, err := parseInt64(value, "false_alarms")
	if err != nil {
		return Metric{}, err
	}
	correctNegatives, err := parseInt64(value, "correct_negatives")
	if err != nil {
		return Metric{}, err
	}
	return Metric{
		CaseID: caseID, CaseCategory: category, IssueTime: issueTime.UTC(),
		TruthKind: truthKind, Model: model, LeadMinutes: lead,
		ThresholdMMH: threshold, WindowPixels: window, WindowKM: windowKM,
		WindowTargetKM: windowTargetKM,
		Hits:           hits, Misses: misses, FalseAlarms: falseAlarms,
		CorrectNegatives: correctNegatives,
		CSI:              nullableFloat(value, "csi"), POD: nullableFloat(value, "pod"),
		FAR: nullableFloat(value, "far"), FSS: nullableFloat(value, "fss"),
		MAEMMH:           nullableFloat(value, "mae_mm_h"),
		RMSEMMH:          nullableFloat(value, "rmse_mm_h"),
		MeanErrorMMH:     nullableFloat(value, "mean_error_mm_h"),
		TruthCoverage:    nullableFloat(value, "truth_coverage"),
		ForecastCoverage: nullableFloat(value, "forecast_coverage"),
		CommonCoverage:   nullableFloat(value, "common_coverage"),
		ForecastToTruthCoverage: nullableFloat(
			optionalValue, "forecast_to_truth_coverage",
		),
		AdvectionDomainToTruthCoverage: nullableFloat(
			optionalValue, "advection_domain_to_truth_coverage",
		),
		AdvectionBoundaryLossRatio: nullableFloat(
			optionalValue, "advection_boundary_loss_ratio",
		),
		InteriorMissingLossRatio: nullableFloat(
			optionalValue, "interior_missing_loss_ratio",
		),
		BoundaryAdjustedCoverage: nullableFloat(
			optionalValue, "boundary_adjusted_forecast_to_truth_coverage",
		),
		CoverageDecompositionClosureErr: nullableFloat(
			optionalValue, "coverage_decomposition_closure_error",
		),
	}, nil
}

func sortedFSSScales(values map[int]FSSScale) []FSSScale {
	keys := make([]int, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Ints(keys)
	result := make([]FSSScale, 0, len(keys))
	for _, key := range keys {
		result = append(result, values[key])
	}
	return result
}

func legacyFSSTargetKM(windowPixels int, actualKM float64) float64 {
	// RP-016 reports predate window_target_km. Their frozen odd-grid windows
	// correspond to the physical scales adopted by RP-018.
	switch windowPixels {
	case 1:
		return 1
	case 5:
		return 5
	case 11:
		return 10
	case 21:
		return 20
	case 41:
		return 40
	default:
		return actualKM
	}
}

func parseInt(value func(string) (string, error), name string) (int, error) {
	text, err := value(name)
	if err != nil {
		return 0, err
	}
	parsed, err := strconv.Atoi(text)
	if err != nil {
		return 0, fmt.Errorf("column %s is invalid: %w", name, err)
	}
	return parsed, nil
}

func parseInt64(value func(string) (string, error), name string) (int64, error) {
	text, err := value(name)
	if err != nil {
		return 0, err
	}
	parsed, err := strconv.ParseInt(text, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("column %s is invalid: %w", name, err)
	}
	return parsed, nil
}

func parseFloat(value func(string) (string, error), name string) (float64, error) {
	text, err := value(name)
	if err != nil {
		return 0, err
	}
	parsed, err := strconv.ParseFloat(text, 64)
	if err != nil || math.IsNaN(parsed) || math.IsInf(parsed, 0) {
		return 0, fmt.Errorf("column %s is invalid", name)
	}
	return parsed, nil
}

func nullableFloat(value func(string) (string, error), name string) *float64 {
	text, err := value(name)
	if err != nil || text == "" {
		return nil
	}
	parsed, err := strconv.ParseFloat(text, 64)
	if err != nil || math.IsNaN(parsed) || math.IsInf(parsed, 0) {
		return nil
	}
	return &parsed
}

func sortedIntKeys(values map[int]bool) []int {
	result := make([]int, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Ints(result)
	return result
}

func sortedFloatKeys(values map[float64]bool) []float64 {
	result := make([]float64, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Float64s(result)
	return result
}

func statFile(path string) (fileStamp, error) {
	info, err := os.Stat(path)
	if err != nil {
		return fileStamp{}, err
	}
	if !info.Mode().IsRegular() {
		return fileStamp{}, fmt.Errorf("%w: report asset is not a regular file", ErrInvalidReport)
	}
	return fileStamp{Size: info.Size(), ModTime: info.ModTime()}, nil
}

func reportFileError(err error) error {
	if errors.Is(err, os.ErrNotExist) {
		return ErrNotFound
	}
	return fmt.Errorf("read algorithm-verification report: %w", err)
}

func validSegment(value string) bool {
	return segmentPattern.MatchString(value)
}

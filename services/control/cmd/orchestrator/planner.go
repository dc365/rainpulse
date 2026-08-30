package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"gopkg.in/yaml.v3"
)

type pipelineSettings struct {
	interval           time.Duration
	lookback           time.Duration
	mosaicDelay        time.Duration
	radarIDs           map[string]struct{}
	qcConfig           string
	gridConfig         string
	mosaicConfig       string
	qpeConfig          string
	diagnosticConfig   string
	nowcastConfig      string
	pystepsConfig      string
	productConfig      string
	verificationConfig string
	gridID             string
	minimumFrames      int
	maximumFrames      int
	forecastEnabled    bool
	requireAllRadars   bool
}

type pipelinePlanner struct {
	settings            pipelineSettings
	store               *postgresstore.Store
	service             *orchestration.Service
	plannedQC           map[uuid.UUID]struct{}
	plannedGrid         map[uuid.UUID]struct{}
	plannedMosaic       map[time.Time]struct{}
	plannedQPE          map[uuid.UUID]struct{}
	plannedDiagnostic   map[uuid.UUID]struct{}
	plannedNowcast      map[time.Time]struct{}
	plannedPysteps      map[uuid.UUID]struct{}
	plannedProduct      map[uuid.UUID]struct{}
	plannedVerification map[uuid.UUID]struct{}
}

func pipelineSettingsFromEnvironment() (*pipelineSettings, error) {
	enabled, err := strconv.ParseBool(environmentOrDefault("RAINPULSE_PIPELINE_ENABLED", "false"))
	if err != nil {
		return nil, fmt.Errorf("parse RAINPULSE_PIPELINE_ENABLED: %w", err)
	}
	if !enabled {
		return nil, nil
	}
	interval, err := time.ParseDuration(environmentOrDefault("RAINPULSE_PIPELINE_INTERVAL", "2s"))
	if err != nil || interval <= 0 {
		return nil, fmt.Errorf("RAINPULSE_PIPELINE_INTERVAL must be a positive duration")
	}
	lookback, err := time.ParseDuration(environmentOrDefault("RAINPULSE_PIPELINE_LOOKBACK", "1h"))
	if err != nil || lookback < 0 {
		return nil, fmt.Errorf("RAINPULSE_PIPELINE_LOOKBACK must be a non-negative duration")
	}
	mosaicDelay, err := time.ParseDuration(
		environmentOrDefault("RAINPULSE_PIPELINE_MOSAIC_DELAY", "2m"),
	)
	if err != nil || mosaicDelay < 0 {
		return nil, fmt.Errorf("RAINPULSE_PIPELINE_MOSAIC_DELAY must be a non-negative duration")
	}
	forecastEnabled, err := strconv.ParseBool(
		environmentOrDefault("RAINPULSE_PIPELINE_FORECAST_ENABLED", "true"),
	)
	if err != nil {
		return nil, fmt.Errorf("parse RAINPULSE_PIPELINE_FORECAST_ENABLED: %w", err)
	}
	requireAllRadars, err := strconv.ParseBool(
		environmentOrDefault("RAINPULSE_PIPELINE_REQUIRE_ALL_RADARS", "false"),
	)
	if err != nil {
		return nil, fmt.Errorf("parse RAINPULSE_PIPELINE_REQUIRE_ALL_RADARS: %w", err)
	}
	radarIDs := make(map[string]struct{})
	for _, raw := range strings.Split(os.Getenv("RAINPULSE_PIPELINE_RADAR_IDS"), ",") {
		if value := strings.TrimSpace(raw); value != "" {
			radarIDs[value] = struct{}{}
		}
	}
	if len(radarIDs) == 0 {
		return nil, fmt.Errorf("enabled real pipeline requires a non-empty radar allowlist")
	}
	settings := &pipelineSettings{
		interval:           interval,
		lookback:           lookback,
		mosaicDelay:        mosaicDelay,
		radarIDs:           radarIDs,
		qcConfig:           environmentOrDefault("RAINPULSE_PIPELINE_QC_CONFIG", "/opt/rainpulse/configs/qc/rp008-basic-v1.yaml"),
		gridConfig:         environmentOrDefault("RAINPULSE_PIPELINE_GRID_CONFIG", "/opt/rainpulse/configs/gridding/rp016-hybrid-v1.yaml"),
		mosaicConfig:       environmentOrDefault("RAINPULSE_PIPELINE_MOSAIC_CONFIG", "/opt/rainpulse/configs/mosaic/rp016-qi-mosaic-v1.yaml"),
		qpeConfig:          environmentOrDefault("RAINPULSE_PIPELINE_QPE_CONFIG", "/opt/rainpulse/configs/qpe/rp011-basic-zr-v1.yaml"),
		diagnosticConfig:   environmentOrDefault("RAINPULSE_PIPELINE_DIAGNOSTIC_CONFIG", "/opt/rainpulse/configs/diagnostics/rp012-operational-diagnostics-v1.yaml"),
		nowcastConfig:      environmentOrDefault("RAINPULSE_PIPELINE_NOWCAST_INPUT_CONFIG", "/opt/rainpulse/configs/nowcast/rp013-fixed-5min-v1.1.yaml"),
		pystepsConfig:      environmentOrDefault("RAINPULSE_PIPELINE_PYSTEPS_CONFIG", "/opt/rainpulse/configs/nowcast/rp016-pysteps-lk-v1.yaml"),
		productConfig:      environmentOrDefault("RAINPULSE_PIPELINE_PRODUCT_CONFIG", "/opt/rainpulse/configs/products/rp015-application-products-v1.yaml"),
		verificationConfig: environmentOrDefault("RAINPULSE_PIPELINE_VERIFICATION_CONFIG", "/opt/rainpulse/configs/verification/rp031-operational-deterministic-v1.yaml"),
		forecastEnabled:    forecastEnabled,
		requireAllRadars:   requireAllRadars,
	}
	for _, path := range []string{
		settings.qcConfig,
		settings.gridConfig,
		settings.mosaicConfig,
		settings.qpeConfig,
		settings.diagnosticConfig,
		settings.nowcastConfig,
		settings.pystepsConfig,
		settings.productConfig,
		settings.verificationConfig,
	} {
		if info, statErr := os.Stat(path); statErr != nil || !info.Mode().IsRegular() {
			return nil, fmt.Errorf("pipeline config must be a readable regular file: %s", path)
		}
	}
	var qc qcConfiguration
	var grid gridConfiguration
	var mosaic mosaicConfiguration
	var qpe qpeConfiguration
	var diagnostic diagnosticConfiguration
	var nowcast nowcastInputConfiguration
	var pysteps pystepsLKConfiguration
	var product productConfiguration
	var verification forecastVerificationConfiguration
	for _, item := range []struct {
		label  string
		path   string
		target any
	}{
		{"QC", settings.qcConfig, &qc},
		{"grid", settings.gridConfig, &grid},
		{"mosaic", settings.mosaicConfig, &mosaic},
		{"QPE", settings.qpeConfig, &qpe},
		{"diagnostic", settings.diagnosticConfig, &diagnostic},
		{"NowcastInput", settings.nowcastConfig, &nowcast},
		{"pySTEPS-LK", settings.pystepsConfig, &pysteps},
		{"product", settings.productConfig, &product},
		{"verification", settings.verificationConfig, &verification},
	} {
		configBytes, readErr := os.ReadFile(item.path)
		if readErr != nil {
			return nil, fmt.Errorf("read pipeline %s config: %w", item.label, readErr)
		}
		if decodeErr := yaml.Unmarshal(configBytes, item.target); decodeErr != nil {
			return nil, fmt.Errorf("decode pipeline %s config: %w", item.label, decodeErr)
		}
	}
	if nowcast.GridID == "" || nowcast.GridConfigVersion == "" ||
		nowcast.Sequence.MinimumFrames < 3 ||
		nowcast.Sequence.MaximumFrames < nowcast.Sequence.MinimumFrames ||
		nowcast.Sequence.TimestepMinutes != 5 {
		return nil, fmt.Errorf("pipeline NowcastInput config has invalid grid or frame limits")
	}
	if qc.ProfileVersion == "" || qc.PipelineVersion == "" ||
		diagnostic.ProfileVersion == "" || diagnostic.RendererVersion == "" {
		return nil, fmt.Errorf("pipeline QC or diagnostic config lacks a version identity")
	}
	for label, identity := range map[string][2]string{
		"grid":       {grid.GridID, grid.GridConfigVersion},
		"mosaic":     {mosaic.GridID, mosaic.GridConfigVersion},
		"QPE":        {qpe.GridID, qpe.GridConfigVersion},
		"pySTEPS-LK": {pysteps.GridID, pysteps.GridConfigVersion},
		"product":    {product.GridID, product.GridConfigVersion},
	} {
		if identity != [2]string{nowcast.GridID, nowcast.GridConfigVersion} {
			return nil, fmt.Errorf("pipeline %s config uses a different grid identity", label)
		}
	}
	if pysteps.Extrapolation.LeadCount != 24 ||
		pysteps.Extrapolation.LeadStepMinutes != 5 {
		return nil, fmt.Errorf("pipeline pySTEPS-LK config must publish 24 five-minute leads")
	}
	if err := validateForecastVerificationConfiguration(verification); err != nil {
		return nil, err
	}
	expectedRadars := make(map[string]struct{}, len(mosaic.Alignment.ExpectedRadarIDs))
	for _, radarID := range mosaic.Alignment.ExpectedRadarIDs {
		expectedRadars[radarID] = struct{}{}
	}
	if len(expectedRadars) > 0 {
		for radarID := range radarIDs {
			if _, expected := expectedRadars[radarID]; !expected {
				return nil, fmt.Errorf("pipeline radar %s is absent from the mosaic inventory", radarID)
			}
		}
	}
	settings.gridID = nowcast.GridID
	settings.minimumFrames = nowcast.Sequence.MinimumFrames
	settings.maximumFrames = nowcast.Sequence.MaximumFrames
	return settings, nil
}

func newPipelinePlanner(
	settings pipelineSettings,
	store *postgresstore.Store,
	service *orchestration.Service,
) *pipelinePlanner {
	return &pipelinePlanner{
		settings: settings, store: store, service: service,
		plannedQC: make(map[uuid.UUID]struct{}), plannedGrid: make(map[uuid.UUID]struct{}),
		plannedMosaic: make(map[time.Time]struct{}), plannedQPE: make(map[uuid.UUID]struct{}),
		plannedDiagnostic: make(map[uuid.UUID]struct{}), plannedNowcast: make(map[time.Time]struct{}),
		plannedPysteps: make(map[uuid.UUID]struct{}), plannedProduct: make(map[uuid.UUID]struct{}),
		plannedVerification: make(map[uuid.UUID]struct{}),
	}
}

func (planner *pipelinePlanner) Run(ctx context.Context) {
	ticker := time.NewTicker(planner.settings.interval)
	defer ticker.Stop()
	for {
		if err := planner.PlanOnce(ctx); err != nil {
			slog.Error("plan RainPulse pipeline", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (planner *pipelinePlanner) PlanOnce(ctx context.Context) error {
	if err := planner.planRadarStage(ctx, workflow.RadarScanNormalized); err != nil {
		return err
	}
	if err := planner.planRadarStage(ctx, workflow.RadarScanQCReady); err != nil {
		return err
	}
	if err := planner.planMosaics(ctx); err != nil {
		return err
	}
	if err := planner.planAnalyses(ctx); err != nil {
		return err
	}
	if !planner.settings.forecastEnabled {
		return nil
	}
	return planner.planForecasts(ctx)
}

func (planner *pipelinePlanner) planRadarStage(ctx context.Context, status workflow.RadarScanStatus) error {
	scans, err := planner.store.ListRadarScans(ctx, 200, nil, &status)
	if err != nil {
		return err
	}
	for _, scan := range scans {
		if _, allowed := planner.settings.radarIDs[scan.RadarID]; !allowed {
			continue
		}
		if planner.outsideLookback(scan.VolumeEndTime) {
			continue
		}
		planned := planner.plannedQC
		config := planner.settings.qcConfig
		command := radarQC
		stage := "qc"
		if status == workflow.RadarScanQCReady {
			planned = planner.plannedGrid
			config = planner.settings.gridConfig
			command = radarGrid
			stage = "grid"
		}
		if _, exists := planned[scan.ID]; exists {
			continue
		}
		if err := command(ctx, planner.store, planner.service, scan.ID.String(), config); err != nil {
			slog.Error("plan radar stage", "stage", stage, "scan_id", scan.ID, "error", err)
			continue
		}
		planned[scan.ID] = struct{}{}
	}
	return nil
}

func (planner *pipelinePlanner) planMosaics(ctx context.Context) error {
	status := workflow.RadarScanGridReady
	scans, err := planner.store.ListRadarScans(ctx, 200, nil, &status)
	if err != nil {
		return err
	}
	byTime := make(map[time.Time][]workflow.RadarScan)
	for _, scan := range scans {
		if _, allowed := planner.settings.radarIDs[scan.RadarID]; !allowed {
			continue
		}
		if planner.outsideLookback(scan.VolumeEndTime) {
			continue
		}
		analysisTime := scan.VolumeEndTime.UTC().Round(5 * time.Minute)
		if absoluteDuration(scan.VolumeEndTime.Sub(analysisTime)) > 150*time.Second {
			continue
		}
		byTime[analysisTime] = append(byTime[analysisTime], scan)
	}
	times := make([]time.Time, 0, len(byTime))
	for analysisTime := range byTime {
		times = append(times, analysisTime)
	}
	sort.Slice(times, func(i, j int) bool { return times[i].Before(times[j]) })
	for _, analysisTime := range times {
		if time.Now().UTC().Before(analysisTime.Add(planner.settings.mosaicDelay)) {
			continue
		}
		if _, exists := planner.plannedMosaic[analysisTime]; exists {
			continue
		}
		selected := closestScanByRadar(byTime[analysisTime], analysisTime)
		if planner.settings.requireAllRadars && len(selected) < len(planner.settings.radarIDs) {
			continue
		}
		scanIDs := make([]string, 0, len(selected))
		for _, scan := range selected {
			scanIDs = append(scanIDs, scan.ID.String())
		}
		sort.Strings(scanIDs)
		if err := analysisMosaic(
			ctx,
			planner.store,
			planner.service,
			analysisTime.Format(time.RFC3339Nano),
			planner.settings.mosaicConfig,
			scanIDs,
		); err != nil {
			slog.Error("plan radar mosaic", "analysis_time", analysisTime, "error", err)
			continue
		}
		planner.plannedMosaic[analysisTime] = struct{}{}
	}
	return nil
}

func (planner *pipelinePlanner) planAnalyses(ctx context.Context) error {
	qpeStatus := workflow.AnalysisQPE
	cycles, err := planner.store.ListAnalysisCycles(ctx, 200, &qpeStatus)
	if err != nil {
		return err
	}
	for _, cycle := range cycles {
		if cycle.GridID != planner.settings.gridID {
			continue
		}
		if planner.outsideLookback(cycle.AnalysisTime) {
			continue
		}
		if _, exists := planner.plannedQPE[cycle.ID]; exists {
			continue
		}
		if err := analysisQPE(
			ctx, planner.store, planner.service, cycle.ID.String(), planner.settings.qpeConfig,
		); err != nil {
			slog.Error("plan analysis QPE", "analysis_id", cycle.ID, "error", err)
			continue
		}
		planner.plannedQPE[cycle.ID] = struct{}{}
	}

	readyStatus := workflow.AnalysisReady
	ready, err := planner.store.ListAnalysisCycles(ctx, 200, &readyStatus)
	if err != nil {
		return err
	}
	for _, cycle := range ready {
		if cycle.GridID != planner.settings.gridID {
			continue
		}
		if planner.outsideLookback(cycle.AnalysisTime) {
			continue
		}
		if _, exists := planner.plannedDiagnostic[cycle.ID]; !exists {
			if err := analysisDiagnostics(
				ctx,
				planner.store,
				planner.service,
				cycle.ID.String(),
				planner.settings.diagnosticConfig,
			); err != nil {
				slog.Error("plan analysis diagnostics", "analysis_id", cycle.ID, "error", err)
			} else {
				planner.plannedDiagnostic[cycle.ID] = struct{}{}
			}
		}
	}
	var latest *workflow.AnalysisCycle
	for index := range ready {
		if ready[index].GridID == planner.settings.gridID &&
			!planner.outsideLookback(ready[index].AnalysisTime) {
			latest = &ready[index]
			break
		}
	}
	if latest == nil {
		return nil
	}
	if _, exists := planner.plannedNowcast[latest.AnalysisTime]; exists {
		return nil
	}
	candidates, err := planner.store.ListNowcastInputCandidates(
		ctx, latest.AnalysisTime, planner.settings.gridID, planner.settings.maximumFrames,
	)
	if err != nil {
		return err
	}
	if len(candidates) < planner.settings.minimumFrames {
		return nil
	}
	if err := nowcastInput(
		ctx,
		planner.store,
		planner.service,
		latest.AnalysisTime.Format(time.RFC3339Nano),
		planner.settings.nowcastConfig,
	); err != nil {
		slog.Error("plan NowcastInput", "issue_time", latest.AnalysisTime, "error", err)
		return nil
	}
	planner.plannedNowcast[latest.AnalysisTime] = struct{}{}
	return nil
}

func (planner *pipelinePlanner) planForecasts(ctx context.Context) error {
	inputReady := workflow.RunInputReady
	runs, _, err := planner.store.ListRuns(ctx, 200, nil, &inputReady)
	if err != nil {
		return err
	}
	for _, run := range runs {
		if run.GridID != planner.settings.gridID {
			continue
		}
		if planner.outsideLookback(run.IssueTime) {
			continue
		}
		if _, exists := planner.plannedPysteps[run.ID]; exists {
			continue
		}
		if err := pystepsLK(
			ctx, planner.store, planner.service, run.ID.String(), planner.settings.pystepsConfig,
		); err != nil {
			slog.Error("plan pySTEPS-LK", "run_id", run.ID, "error", err)
			continue
		}
		planner.plannedPysteps[run.ID] = struct{}{}
	}

	baselineReady := workflow.RunBaselineReady
	runs, _, err = planner.store.ListRuns(ctx, 200, nil, &baselineReady)
	if err != nil {
		return err
	}
	for _, run := range runs {
		if run.GridID != planner.settings.gridID {
			continue
		}
		if planner.outsideLookback(run.IssueTime) {
			continue
		}
		if _, exists := planner.plannedProduct[run.ID]; exists {
			continue
		}
		if err := productBuild(
			ctx, planner.store, planner.service, run.ID.String(), planner.settings.productConfig,
		); err != nil {
			slog.Error("plan application products", "run_id", run.ID, "error", err)
			continue
		}
		planner.plannedProduct[run.ID] = struct{}{}
	}

	published := workflow.RunPublished
	runs, _, err = planner.store.ListRuns(ctx, 200, nil, &published)
	if err != nil {
		return err
	}
	for _, run := range runs {
		if run.GridID != planner.settings.gridID || planner.outsideVerificationLookback(run.IssueTime) {
			continue
		}
		if _, exists := planner.plannedVerification[run.ID]; exists {
			continue
		}
		input, inputErr := planner.store.GetForecastVerificationInput(ctx, run.ID)
		if inputErr != nil {
			slog.Error("inspect forecast verification truth", "run_id", run.ID, "error", inputErr)
			continue
		}
		if len(input.Truth) != 24 {
			continue
		}
		if err := forecastVerification(
			ctx, planner.store, planner.service, run.ID.String(), planner.settings.verificationConfig,
		); err != nil {
			slog.Error("plan forecast verification", "run_id", run.ID, "error", err)
			continue
		}
		planner.plannedVerification[run.ID] = struct{}{}
	}
	return nil
}

func (planner *pipelinePlanner) outsideLookback(value time.Time) bool {
	return planner.settings.lookback > 0 &&
		time.Since(value.UTC()) > planner.settings.lookback
}

func (planner *pipelinePlanner) outsideVerificationLookback(value time.Time) bool {
	lookback := 6 * time.Hour
	if planner.settings.lookback > lookback {
		lookback = planner.settings.lookback
	}
	return time.Since(value.UTC()) > lookback
}

func closestScanByRadar(scans []workflow.RadarScan, analysisTime time.Time) []workflow.RadarScan {
	selected := make(map[string]workflow.RadarScan)
	for _, scan := range scans {
		current, exists := selected[scan.RadarID]
		if !exists || absoluteDuration(scan.VolumeEndTime.Sub(analysisTime)) <
			absoluteDuration(current.VolumeEndTime.Sub(analysisTime)) {
			selected[scan.RadarID] = scan
		}
	}
	result := make([]workflow.RadarScan, 0, len(selected))
	for _, scan := range selected {
		result = append(result, scan)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].RadarID < result[j].RadarID })
	return result
}

func absoluteDuration(value time.Duration) time.Duration {
	if value < 0 {
		return -value
	}
	return value
}

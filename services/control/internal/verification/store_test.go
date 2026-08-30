package verification

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestFileStoreListsAndFiltersAlgorithmVerificationRuns(t *testing.T) {
	root := t.TempDir()
	writeReportFixture(t, root, "rp016-generic-v1", "full-202108-v2", 6)
	store := NewFileStore(root)

	runs, err := store.ListRuns(context.Background())
	if err != nil {
		t.Fatalf("list runs: %v", err)
	}
	if len(runs) != 1 || runs[0].RunID != "full-202108-v2" || runs[0].SkillStatus != "lk_supported" {
		t.Fatalf("unexpected run summaries: %#v", runs)
	}

	detail, err := store.GetRun(context.Background(), "rp016-generic-v1", "full-202108-v2")
	if err != nil {
		t.Fatalf("get run: %v", err)
	}
	if len(detail.Cases) != 1 || detail.Cases[0].CaseID != "midwest_case" ||
		len(detail.Cases[0].IssueTimes) != 1 {
		t.Fatalf("unexpected cases: %#v", detail.Cases)
	}
	if got := fmt.Sprint(detail.Filters.Models); got != "[lk persistence translation]" {
		t.Fatalf("unexpected models: %s", got)
	}
	if got := fmt.Sprint(detail.Filters.LeadMinutes); got != "[10 20]" {
		t.Fatalf("unexpected leads: %s", got)
	}
	if len(detail.Filters.FSSScales) != 1 || detail.Filters.FSSScales[0].TargetKM != 10 ||
		detail.Filters.FSSScales[0].ActualKMMin != 11.1 || detail.Filters.FSSScales[0].ActualKMMax != 11.1 {
		t.Fatalf("unexpected FSS scales: %#v", detail.Filters.FSSScales)
	}

	issueTime := time.Date(2021, 8, 10, 17, 0, 0, 0, time.UTC)
	metrics, err := store.ListMetrics(
		context.Background(),
		"rp016-generic-v1",
		"full-202108-v2",
		MetricFilter{CaseID: "midwest_case", IssueTime: issueTime, ThresholdMMH: 5, WindowPixels: 11},
	)
	if err != nil {
		t.Fatalf("list metrics: %v", err)
	}
	if len(metrics) != 6 || metrics[0].Model != "lk" || metrics[0].FSS == nil {
		t.Fatalf("unexpected metrics: %#v", metrics)
	}
	if metrics[1].FSS != nil {
		t.Fatalf("expected CSV nan to be represented as nil, got %v", *metrics[1].FSS)
	}
}

func TestFileStoreListsNewestRunFirstAcrossProfilesWithDifferentCaseCounts(t *testing.T) {
	root := t.TempDir()
	writeReportFixture(t, root, "rp018-mrms-v1", "older-53-issues", 6)
	writeReportFixture(t, root, "rp021-mrms-holdout-v1", "newer-50-issues", 6)
	olderPath := filepath.Join(root, "rp018-mrms-v1", "older-53-issues", "summary.json")
	newerPath := filepath.Join(root, "rp021-mrms-holdout-v1", "newer-50-issues", "summary.json")
	olderPayload, err := os.ReadFile(olderPath)
	if err != nil {
		t.Fatalf("read older summary: %v", err)
	}
	olderPayload = []byte(strings.Replace(
		string(olderPayload),
		`"completed_issue_count": 1`,
		`"completed_issue_count": 53`,
		1,
	))
	if err := os.WriteFile(olderPath, olderPayload, 0o644); err != nil {
		t.Fatalf("write older summary: %v", err)
	}
	olderTime := time.Date(2026, 8, 28, 0, 0, 0, 0, time.UTC)
	newerTime := olderTime.Add(24 * time.Hour)
	if err := os.Chtimes(olderPath, olderTime, olderTime); err != nil {
		t.Fatalf("set older summary time: %v", err)
	}
	if err := os.Chtimes(newerPath, newerTime, newerTime); err != nil {
		t.Fatalf("set newer summary time: %v", err)
	}

	runs, err := NewFileStore(root).ListRuns(context.Background())
	if err != nil {
		t.Fatalf("list runs: %v", err)
	}
	if len(runs) != 2 || runs[0].RunID != "newer-50-issues" || runs[1].RunID != "older-53-issues" {
		t.Fatalf("expected reports to be sorted by recency across profiles: %#v", runs)
	}
}

func TestFileStoreLoadsProbabilisticEnsembleSummaryWithoutDeterministicMetrics(t *testing.T) {
	root := t.TempDir()
	writeProbabilisticReportFixture(t, root, "rp026-mrms-nowcastnet-v1", "holdout-v1", false)
	store := NewFileStore(root)

	runs, err := store.ListRuns(context.Background())
	if err != nil {
		t.Fatalf("list probabilistic runs: %v", err)
	}
	if len(runs) != 1 || runs[0].VerificationKind != "probabilistic_ensemble" ||
		runs[0].SkillStatus != "steps_retained_nowcastnet_offline" ||
		runs[0].MapsAvailable || runs[0].OperationalEligible {
		t.Fatalf("unexpected probabilistic run summary: %#v", runs)
	}

	detail, err := store.GetRun(
		context.Background(), "rp026-mrms-nowcastnet-v1", "holdout-v1",
	)
	if err != nil {
		t.Fatalf("get probabilistic run: %v", err)
	}
	if detail.ProbabilisticSummary == nil || detail.ProbabilisticSummary.Split != "holdout" ||
		detail.ProbabilisticSummary.CandidateMemberCount != 4 ||
		detail.ProbabilisticSummary.ReferenceMemberCount != 12 ||
		len(detail.ProbabilisticSummary.LeadBands) != 2 ||
		detail.ProbabilisticSummary.LeadBands[0].CandidateSkills[1].CRPSSkill != 0.2 {
		t.Fatalf("unexpected probabilistic detail: %#v", detail.ProbabilisticSummary)
	}
	if len(detail.Cases) != 0 || len(detail.Filters.Models) != 5 ||
		len(detail.SkillSummary.Comparisons) != 0 {
		t.Fatalf("probabilistic run leaked deterministic selectors: %#v", detail)
	}
	if detail.Filters.LeadMinutes == nil || detail.Filters.ThresholdsMMH == nil ||
		detail.Filters.WindowsPixels == nil || detail.Filters.FSSScales == nil {
		t.Fatalf("probabilistic empty filter dimensions must encode as arrays: %#v", detail.Filters)
	}
}

func TestFileStoreLoadsProbabilisticSpatialEvidenceWithoutEnablingPublication(t *testing.T) {
	root := t.TempDir()
	writeProbabilisticReportFixture(t, root, "rp026-mrms-nowcastnet-v1", "holdout-map-v1", false)
	writeMapFixtureForModels(
		t, root, "rp026-mrms-nowcastnet-v1", "holdout-map-v1", []string{"nowcastnet", "steps"},
	)
	png := writeProbabilityMapFixture(
		t, root, "rp026-mrms-nowcastnet-v1", "holdout-map-v1",
	)
	store := NewFileStore(root)

	runs, err := store.ListRuns(context.Background())
	if err != nil {
		t.Fatalf("list probabilistic map run: %v", err)
	}
	if len(runs) != 1 || !runs[0].MapsAvailable || !runs[0].ProbabilityMapsAvailable ||
		runs[0].MapBundleCount != 1 || runs[0].MapLayerCount != 3 ||
		runs[0].ProbabilityMapBundleCount != 1 || runs[0].ProbabilityMapLayerCount != 15 ||
		runs[0].OperationalEligible {
		t.Fatalf("unexpected probabilistic map summary: %#v", runs)
	}

	detail, err := store.GetRun(
		context.Background(), "rp026-mrms-nowcastnet-v1", "holdout-map-v1",
	)
	if err != nil {
		t.Fatalf("get probabilistic map run: %v", err)
	}
	if len(detail.Cases) != 1 || detail.Cases[0].CaseID != "midwest_case" ||
		fmt.Sprint(detail.Filters.LeadMinutes) != "[10]" ||
		fmt.Sprint(detail.Filters.ThresholdsMMH) != "[1 5 10 20 50]" {
		t.Fatalf("unexpected probabilistic map selectors: %#v", detail)
	}

	frame, err := store.GetMapFrame(
		context.Background(), "rp026-mrms-nowcastnet-v1", "holdout-map-v1",
		MapFrameFilter{
			CaseID: "midwest_case", IssueTime: time.Date(2021, 8, 10, 17, 0, 0, 0, time.UTC),
			LeadMinutes: 10,
		},
	)
	if err != nil {
		t.Fatalf("get probabilistic map frame: %v", err)
	}
	if len(frame.Layers) != 3 || frame.Layers[1].Model == nil ||
		*frame.Layers[1].Model != "nowcastnet" || frame.Layers[2].Model == nil ||
		*frame.Layers[2].Model != "steps" {
		t.Fatalf("unexpected probabilistic map frame: %#v", frame)
	}
	probabilityFrame, err := store.GetProbabilityMapFrame(
		context.Background(), "rp026-mrms-nowcastnet-v1", "holdout-map-v1",
		ProbabilityMapFrameFilter{
			CaseID: "midwest_case", IssueTime: time.Date(2021, 8, 10, 17, 0, 0, 0, time.UTC),
			LeadMinutes: 10, ThresholdMMH: 5,
		},
	)
	if err != nil {
		t.Fatalf("get probability map frame: %v", err)
	}
	if len(probabilityFrame.Layers) != 3 || probabilityFrame.ThresholdMMH != 5 ||
		probabilityFrame.CalibrationStatus != "raw_ensemble_relative_frequency_uncalibrated" ||
		probabilityFrame.OperationalEligible || probabilityFrame.ProductPublicationEnabled {
		t.Fatalf("unexpected probability map frame: %#v", probabilityFrame)
	}
	asset, err := store.ReadProbabilityMapAsset(
		context.Background(), "rp026-mrms-nowcastnet-v1", "holdout-map-v1",
		"midwest_case", "20210810T170000Z", "lead-010-threshold-005-truth",
	)
	if err != nil || string(asset.Data) != string(png) {
		t.Fatalf("read probability map asset: asset=%#v err=%v", asset, err)
	}
}

func TestFileStoreRejectsProbabilisticReportThatEnablesPublication(t *testing.T) {
	root := t.TempDir()
	writeProbabilisticReportFixture(t, root, "rp026-mrms-nowcastnet-v1", "unsafe", true)
	_, err := NewFileStore(root).GetRun(
		context.Background(), "rp026-mrms-nowcastnet-v1", "unsafe",
	)
	if !errors.Is(err, ErrInvalidReport) {
		t.Fatalf("expected unsafe probabilistic report to be rejected, got %v", err)
	}
}

func TestFileStoreUsesFixedTruthDomainForRigorousReports(t *testing.T) {
	root := t.TempDir()
	writeRigorousReportFixture(t, root, "rp018-mrms-v1", "full-v1")
	store := NewFileStore(root)

	detail, err := store.GetRun(context.Background(), "rp018-mrms-v1", "full-v1")
	if err != nil {
		t.Fatalf("get rigorous run: %v", err)
	}
	if got := fmt.Sprint(detail.Filters.Models); got != "[lk persistence translation phase_correlation]" {
		t.Fatalf("unexpected rigorous display models: %s", got)
	}
	if got := fmt.Sprint(detail.Filters.WindowsPixels); got != "[9]" {
		t.Fatalf("unexpected rigorous scale selectors: %s", got)
	}
	if len(detail.Filters.FSSScales) != 1 || detail.Filters.FSSScales[0].TargetKM != 10 ||
		detail.Filters.FSSScales[0].WindowPixels != 9 ||
		detail.Filters.FSSScales[0].ActualKMMin != 9.1 ||
		detail.Filters.FSSScales[0].ActualKMMax != 11.1 {
		t.Fatalf("unexpected rigorous FSS scales: %#v", detail.Filters.FSSScales)
	}
	issueTime := time.Date(2021, 8, 10, 17, 0, 0, 0, time.UTC)
	metrics, err := store.ListMetrics(
		context.Background(),
		"rp018-mrms-v1",
		"full-v1",
		MetricFilter{CaseID: "midwest_case", IssueTime: issueTime, ThresholdMMH: 5, WindowPixels: 9},
	)
	if err != nil {
		t.Fatalf("list rigorous metrics: %v", err)
	}
	if len(metrics) != 10 {
		t.Fatalf("expected all fixed-domain metric rows, got %d", len(metrics))
	}
	models := make(map[string]bool)
	for _, metric := range metrics {
		models[metric.Model] = true
	}
	if !models["phase_correlation"] || !models["lk_native_10min"] {
		t.Fatalf("fixed truth-domain models are incomplete: %#v", models)
	}
	if metrics[0].WindowPixels != 11 || metrics[0].WindowTargetKM != 10 {
		t.Fatalf("expected case-specific 11-pixel realization of 10 km, got %#v", metrics[0])
	}
}

func TestFileStoreRejectsUnknownRunsAndCountDrift(t *testing.T) {
	root := t.TempDir()
	writeReportFixture(t, root, "rp016-generic-v1", "broken", 7)
	store := NewFileStore(root)

	if _, err := store.GetRun(context.Background(), "../escape", "broken"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected traversal to be rejected as not found, got %v", err)
	}
	if _, err := store.GetRun(context.Background(), "rp016-generic-v1", "missing"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected missing run, got %v", err)
	}
	if _, err := store.GetRun(context.Background(), "rp016-generic-v1", "broken"); !errors.Is(err, ErrInvalidReport) {
		t.Fatalf("expected metric count drift, got %v", err)
	}
}

func TestFileStoreTreatsMissingRootAsEmpty(t *testing.T) {
	store := NewFileStore(filepath.Join(t.TempDir(), "not-created"))
	runs, err := store.ListRuns(context.Background())
	if err != nil || len(runs) != 0 {
		t.Fatalf("expected empty missing root, got runs=%#v err=%v", runs, err)
	}
}

func TestParseMetricReadsExplicitPhysicalFSSTarget(t *testing.T) {
	header := append(append([]string{}, metricColumns...), "window_target_km")
	columns := make(map[string]int, len(header))
	for index, name := range header {
		columns[name] = index
	}
	row := strings.Split(metricFixtureRow("lk", 10, "0.72")+",9.5", ",")

	metric, err := parseMetric(row, columns)
	if err != nil {
		t.Fatalf("parse metric: %v", err)
	}
	if metric.WindowPixels != 11 || metric.WindowKM != 11.1 || metric.WindowTargetKM != 9.5 {
		t.Fatalf("unexpected physical FSS scale: %#v", metric)
	}
}

func TestParseMetricReadsOptionalCoverageProvenance(t *testing.T) {
	provenanceColumns := []string{
		"forecast_to_truth_coverage",
		"advection_domain_to_truth_coverage",
		"advection_boundary_loss_ratio",
		"interior_missing_loss_ratio",
		"boundary_adjusted_forecast_to_truth_coverage",
		"coverage_decomposition_closure_error",
	}
	header := append(append([]string{}, metricColumns...), provenanceColumns...)
	columns := make(map[string]int, len(header))
	for index, name := range header {
		columns[name] = index
	}
	row := strings.Split(metricFixtureRow("lk", 10, "0.72")+",0.98,0.98,0.02,0,1,0", ",")

	metric, err := parseMetric(row, columns)
	if err != nil {
		t.Fatalf("parse metric coverage provenance: %v", err)
	}
	if metric.ForecastToTruthCoverage == nil || *metric.ForecastToTruthCoverage != 0.98 ||
		metric.AdvectionBoundaryLossRatio == nil || *metric.AdvectionBoundaryLossRatio != 0.02 ||
		metric.InteriorMissingLossRatio == nil || *metric.InteriorMissingLossRatio != 0 ||
		metric.BoundaryAdjustedCoverage == nil || *metric.BoundaryAdjustedCoverage != 1 ||
		metric.CoverageDecompositionClosureErr == nil ||
		*metric.CoverageDecompositionClosureErr != 0 {
		t.Fatalf("unexpected coverage provenance: %#v", metric)
	}
}

func TestFileStoreReadsOnlyManifestListedVerificationMapAssets(t *testing.T) {
	root := t.TempDir()
	writeReportFixture(t, root, "rp016-generic-v1", "mapped-run", 6)
	png := writeMapFixture(t, root, "rp016-generic-v1", "mapped-run")
	store := NewFileStore(root)
	issueTime := time.Date(2021, 8, 10, 17, 0, 0, 0, time.UTC)

	frame, err := store.GetMapFrame(
		context.Background(),
		"rp016-generic-v1",
		"mapped-run",
		MapFrameFilter{CaseID: "midwest_case", IssueTime: issueTime, LeadMinutes: 10},
	)
	if err != nil {
		t.Fatalf("get map frame: %v", err)
	}
	if frame.RendererVersion != "verification-renderer-v1" || len(frame.Layers) != 2 ||
		frame.Layers[0].ValidTime != issueTime.Add(10*time.Minute) || len(frame.Motion.Vectors) != 1 {
		t.Fatalf("unexpected map frame: %#v", frame)
	}
	asset, err := store.ReadMapAsset(
		context.Background(),
		"rp016-generic-v1",
		"mapped-run",
		"midwest_case",
		"20210810T170000Z",
		"lead-010-truth",
	)
	if err != nil {
		t.Fatalf("read map asset: %v", err)
	}
	if string(asset.Data) != string(png) || asset.SHA256 != fmt.Sprintf("%x", sha256.Sum256(png)) {
		t.Fatalf("unexpected map asset content: %#v", asset)
	}
	if _, err := store.ReadMapAsset(
		context.Background(), "rp016-generic-v1", "mapped-run", "../escape",
		"20210810T170000Z", "lead-010-truth",
	); !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected map path traversal to be rejected, got %v", err)
	}
	if _, err := store.ReadMapAsset(
		context.Background(), "rp016-generic-v1", "mapped-run", "midwest_case",
		"20210810T170000Z", "unlisted",
	); !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected unlisted map asset to be rejected, got %v", err)
	}
}

func writeReportFixture(
	t *testing.T,
	root string,
	profileVersion string,
	runID string,
	metricRowCount int,
) {
	t.Helper()
	directory := filepath.Join(root, profileVersion, runID)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatalf("create report fixture: %v", err)
	}
	summary := fmt.Sprintf(`{
  "schema_version": "1.0",
  "profile_version": %q,
  "primary_truth_kind": "observed_rate",
  "operational_eligible": false,
  "completed_issue_count": 1,
  "failed_issue_count": 0,
  "motion_fallback_issue_count": 0,
  "metric_row_count": %d,
  "errors": [],
  "skill_summary": {
    "status": "lk_supported",
    "comparison_metric": "FSS",
    "comparisons": [{
      "baseline": "persistence",
      "bootstrap_sample_count": 2000,
      "case_mean_differences": {"midwest_case": 0.02},
      "evaluable_case_count": 1,
      "maximum_lead_minutes": 60,
      "mean_difference_95pct_interval": [0.01, 0.03],
      "mean_fss_difference": 0.02,
      "passes_case_gate": true,
      "positive_case_count": 1,
      "threshold_mm_h": 5,
      "total_wet_case_count": 1,
      "window_pixels": 11
    }]
  }
}`, profileVersion, metricRowCount)
	if err := os.WriteFile(filepath.Join(directory, "summary.json"), []byte(summary), 0o644); err != nil {
		t.Fatalf("write summary fixture: %v", err)
	}
	header := strings.Join(metricColumns, ",") + "\n"
	rows := []string{
		metricFixtureRow("lk", 10, "0.72"),
		metricFixtureRow("lk", 20, "nan"),
		metricFixtureRow("persistence", 10, "0.68"),
		metricFixtureRow("persistence", 20, "0.61"),
		metricFixtureRow("translation", 10, "0.70"),
		metricFixtureRow("translation", 20, "0.65"),
	}
	if err := os.WriteFile(
		filepath.Join(directory, "metrics.csv"),
		[]byte(header+strings.Join(rows, "\n")+"\n"),
		0o644,
	); err != nil {
		t.Fatalf("write metrics fixture: %v", err)
	}
}

func writeProbabilisticReportFixture(
	t *testing.T,
	root string,
	profileVersion string,
	runID string,
	productPublicationEnabled bool,
) {
	t.Helper()
	directory := filepath.Join(root, profileVersion, runID)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatalf("create probabilistic report fixture: %v", err)
	}
	models := []string{"nowcastnet", "steps", "lk", "persistence", "phase_correlation"}
	scores := make(map[string]any, len(models))
	for index, model := range models {
		scores[model] = map[string]any{
			"brier_score_by_threshold":  map[string]float64{"1.0": 0.1 + float64(index)/100},
			"crps_mm_h":                 1.5 + float64(index)/10,
			"ensemble_mean_rmse_mm_h":   3.5 + float64(index)/10,
			"mean_ensemble_spread_mm_h": float64(index) / 10,
		}
	}
	skills := map[string]any{}
	for index, model := range models[1:] {
		skills[model] = map[string]any{
			"brier_skill_by_threshold": map[string]float64{"1.0": 0.1},
			"crps_skill":               float64(index+1) / 10,
		}
	}
	band := func(minimum int, maximum int) map[string]any {
		return map[string]any{
			"lead_minutes":                            []int{minimum, maximum},
			"minimum_common_verification_coverage":    0.72,
			"minimum_nowcastnet_member_mean_coverage": 1.0,
			"minimum_steps_member_mean_coverage":      0.9,
			"scores":                                  scores,
			"nowcastnet_skill":                        skills,
		}
	}
	quantiles := map[string]float64{"p50": 100, "p95": 150, "max": 200}
	summary := map[string]any{
		"schema_version":              "1.0",
		"profile_version":             profileVersion,
		"split":                       "holdout",
		"calibration_status":          "raw_ensemble_relative_frequency_uncalibrated",
		"operational_eligible":        false,
		"product_publication_enabled": productPublicationEnabled,
		"completed_issue_count":       50,
		"failed_issue_count":          0,
		"motion_fallback_issue_count": 2,
		"metric_row_count":            3000,
		"nowcastnet_member_count":     4,
		"steps_member_count":          12,
		"models":                      models,
		"lead_minutes":                []int{10},
		"thresholds_mm_h":             []float64{1, 5, 10, 20, 50},
		"lead_band_summary":           map[string]any{"near": band(10, 60), "far": band(70, 120)},
		"performance_summary": map[string]any{
			"nowcastnet_runtime_ms":    quantiles,
			"steps_runtime_ms":         quantiles,
			"total_runtime_ms":         quantiles,
			"gpu_peak_allocated_bytes": quantiles,
			"peak_rss_bytes":           quantiles,
		},
		"runtime": map[string]string{"device_name": "NVIDIA RTX 6000D"},
	}
	encoded, err := json.Marshal(summary)
	if err != nil {
		t.Fatalf("encode probabilistic report fixture: %v", err)
	}
	if err := os.WriteFile(filepath.Join(directory, "summary.json"), encoded, 0o644); err != nil {
		t.Fatalf("write probabilistic report fixture: %v", err)
	}
}

func writeRigorousReportFixture(
	t *testing.T,
	root string,
	profileVersion string,
	runID string,
) {
	t.Helper()
	writeReportFixture(t, root, profileVersion, runID, 6)
	directory := filepath.Join(root, profileVersion, runID)
	summaryPath := filepath.Join(directory, "summary.json")
	var summary map[string]any
	if err := json.Unmarshal(mustReadFile(t, summaryPath), &summary); err != nil {
		t.Fatalf("decode rigorous summary fixture: %v", err)
	}
	summary["schema_version"] = "1.2"
	summary["truth_domain_metric_row_count"] = 20
	summary["report_files"] = map[string]any{"fixed_truth_domain": "metrics_truth_domain.csv"}
	skill := summary["skill_summary"].(map[string]any)
	skill["candidate_model"] = "lk"
	skill["comparisons"] = []map[string]any{
		{"baseline": "persistence"},
		{"baseline": "translation"},
		{"baseline": "phase_correlation"},
	}
	encoded, err := json.Marshal(summary)
	if err != nil {
		t.Fatalf("encode rigorous summary fixture: %v", err)
	}
	if err := os.WriteFile(summaryPath, encoded, 0o644); err != nil {
		t.Fatalf("write rigorous summary fixture: %v", err)
	}
	header := strings.Join(append(append([]string{}, metricColumns...), "window_target_km"), ",") + "\n"
	rows := make([]string, 0, 20)
	for _, model := range []string{
		"lk", "persistence", "translation", "phase_correlation", "lk_native_10min",
	} {
		rows = append(rows, rigorousMetricFixtureRow(model, 10, "0.72", 9, 9.1, "socal_case"))
		rows = append(rows, rigorousMetricFixtureRow(model, 20, "0.68", 9, 9.1, "socal_case"))
		rows = append(rows, rigorousMetricFixtureRow(model, 10, "0.72", 11, 11.1, "midwest_case"))
		rows = append(rows, rigorousMetricFixtureRow(model, 20, "0.68", 11, 11.1, "midwest_case"))
	}
	if err := os.WriteFile(
		filepath.Join(directory, "metrics_truth_domain.csv"),
		[]byte(header+strings.Join(rows, "\n")+"\n"),
		0o644,
	); err != nil {
		t.Fatalf("write fixed truth-domain metrics fixture: %v", err)
	}
}

func rigorousMetricFixtureRow(
	model string,
	lead int,
	fss string,
	windowPixels int,
	windowKM float64,
	caseID string,
) string {
	return fmt.Sprintf(
		"%s,%d,5,%d,%.1f,10,2,1,88,0.76,0.83,0.09,%s,0.8,1.2,0.1,1,0.99,0.99,%s,wet,2021-08-10T17:00:00Z,observed_rate,10",
		model,
		lead,
		windowPixels,
		windowKM,
		fss,
		caseID,
	)
}

func metricFixtureRow(model string, lead int, fss string) string {
	return fmt.Sprintf(
		"%s,%d,5,11,11.1,10,2,1,88,0.76,0.83,0.09,%s,0.8,1.2,0.1,1,0.99,0.99,midwest_case,wet,2021-08-10T17:00:00Z,observed_rate",
		model,
		lead,
		fss,
	)
}

func writeMapFixture(t *testing.T, root string, profileVersion string, runID string) []byte {
	t.Helper()
	return writeMapFixtureForModels(t, root, profileVersion, runID, []string{"lk"})
}

func writeMapFixtureForModels(
	t *testing.T,
	root string,
	profileVersion string,
	runID string,
	models []string,
) []byte {
	t.Helper()
	directory := filepath.Join(root, profileVersion, runID)
	summaryPath := filepath.Join(directory, "summary.json")
	var summary map[string]any
	if err := json.Unmarshal(mustReadFile(t, summaryPath), &summary); err != nil {
		t.Fatalf("decode report summary fixture: %v", err)
	}
	summary["map_bundle_count"] = 1
	summary["map_layer_count"] = 1 + len(models)
	summary["map_renderer_version"] = "verification-renderer-v1"
	encodedSummary, err := json.Marshal(summary)
	if err != nil {
		t.Fatalf("encode report summary fixture: %v", err)
	}
	if err := os.WriteFile(summaryPath, encodedSummary, 0o644); err != nil {
		t.Fatalf("write report summary fixture: %v", err)
	}

	png, err := base64.StdEncoding.DecodeString(
		"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X7WmAAAAAElFTkSuQmCC",
	)
	if err != nil {
		t.Fatalf("decode PNG fixture: %v", err)
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(png))
	issueDirectory := filepath.Join(directory, "maps", "midwest_case", "20210810T170000Z")
	if err := os.MkdirAll(filepath.Join(issueDirectory, "layers"), 0o755); err != nil {
		t.Fatalf("create map fixture: %v", err)
	}
	layers := []map[string]any{}
	identities := []struct {
		assetID string
		role    string
		model   any
	}{
		{assetID: "lead-010-truth", role: "truth", model: nil},
	}
	for _, model := range models {
		identities = append(identities, struct {
			assetID string
			role    string
			model   any
		}{assetID: "lead-010-" + strings.ReplaceAll(model, "_", "-"), role: "forecast", model: model})
	}
	for _, identity := range identities {
		objectPath := "layers/" + identity.assetID + ".png"
		if err := os.WriteFile(filepath.Join(issueDirectory, filepath.FromSlash(objectPath)), png, 0o644); err != nil {
			t.Fatalf("write map PNG fixture: %v", err)
		}
		layers = append(layers, map[string]any{
			"asset_id": identity.assetID, "role": identity.role, "model": identity.model,
			"lead_minutes": 10, "valid_time_utc": "2021-08-10T17:10:00Z",
			"object_path": objectPath, "media_type": "image/png", "sha256": digest,
			"size_bytes": len(png), "width": 1, "height": 1,
			"valid_cell_count": 1, "no_rain_cell_count": 1, "rain_cell_count": 0,
			"missing_cell_count": 0,
		})
	}
	manifest := map[string]any{
		"contract_version": "1.0", "renderer_version": "verification-renderer-v1",
		"render_profile_version": "verification-map-v1", "palette_version": "rainfall-operational-v1",
		"verification_profile_version": profileVersion, "case_id": "midwest_case",
		"issue_key": "20210810T170000Z", "issue_time_utc": "2021-08-10T17:00:00Z",
		"truth_kind": "observed_rate", "operational_eligible": false,
		"grid": map[string]any{
			"grid_id": "grid", "grid_config_version": "grid-v1", "projection": "EPSG:4326",
			"fit_bounds":        []float64{-95, 39, -90, 41},
			"pixel_edge_bounds": []float64{-95.005, 38.995, -89.995, 41.005},
			"width":             1, "height": 1,
		},
		"palette": map[string]any{
			"rain_threshold_mm_h": 0.1, "valid_no_rain_color": "#dce6e2",
			"stops": []map[string]any{{"minimum": 0.1, "color": "#9dd9ff"}},
		},
		"motion": map[string]any{
			"fallback_used": false, "fallback_reason": nil, "feature_count": 4,
			"trackable_rain_pixel_count": 10, "unit": "grid_cells_per_5_minutes",
			"vectors": []map[string]any{{
				"longitude": -92.5, "latitude": 40, "end_longitude": -92.49,
				"end_latitude": 40, "u_pixels_per_step": 1, "v_pixels_per_step": 0,
			}},
		},
		"lead_minutes": []int{10}, "layers": layers,
	}
	encodedManifest, err := json.Marshal(manifest)
	if err != nil {
		t.Fatalf("encode map manifest fixture: %v", err)
	}
	if err := os.WriteFile(filepath.Join(issueDirectory, "manifest.json"), encodedManifest, 0o644); err != nil {
		t.Fatalf("write map manifest fixture: %v", err)
	}
	index := map[string]any{
		"contract_version": "1.0", "verification_profile_version": profileVersion,
		"renderer_version": "verification-renderer-v1", "bundle_count": 1,
		"layer_count": 1 + len(models), "issues": []map[string]any{{
			"case_id": "midwest_case", "issue_time_utc": "2021-08-10T17:00:00Z",
			"issue_key":     "20210810T170000Z",
			"manifest_path": "midwest_case/20210810T170000Z/manifest.json",
			"layer_count":   1 + len(models),
		}},
	}
	encodedIndex, err := json.Marshal(index)
	if err != nil {
		t.Fatalf("encode map index fixture: %v", err)
	}
	if err := os.WriteFile(filepath.Join(directory, "maps", "index.json"), encodedIndex, 0o644); err != nil {
		t.Fatalf("write map index fixture: %v", err)
	}
	return png
}

func writeProbabilityMapFixture(
	t *testing.T,
	root string,
	profileVersion string,
	runID string,
) []byte {
	t.Helper()
	directory := filepath.Join(root, profileVersion, runID)
	summaryPath := filepath.Join(directory, "summary.json")
	var summary map[string]any
	if err := json.Unmarshal(mustReadFile(t, summaryPath), &summary); err != nil {
		t.Fatalf("decode probability map summary fixture: %v", err)
	}
	summary["probability_map_bundle_count"] = 1
	summary["probability_map_layer_count"] = 15
	summary["probability_map_renderer_version"] = "probability-renderer-v1"
	encodedSummary, err := json.Marshal(summary)
	if err != nil {
		t.Fatalf("encode probability map summary fixture: %v", err)
	}
	if err := os.WriteFile(summaryPath, encodedSummary, 0o644); err != nil {
		t.Fatalf("write probability map summary fixture: %v", err)
	}

	png, err := base64.StdEncoding.DecodeString(
		"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X7WmAAAAAElFTkSuQmCC",
	)
	if err != nil {
		t.Fatalf("decode probability PNG fixture: %v", err)
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(png))
	issueDirectory := filepath.Join(directory, "probability-maps", "midwest_case", "20210810T170000Z")
	if err := os.MkdirAll(filepath.Join(issueDirectory, "layers"), 0o755); err != nil {
		t.Fatalf("create probability map fixture: %v", err)
	}
	layers := []map[string]any{}
	for _, threshold := range []int{1, 5, 10, 20, 50} {
		for _, identity := range []struct {
			name  string
			role  string
			model any
		}{
			{name: "truth", role: "truth", model: nil},
			{name: "nowcastnet", role: "forecast", model: "nowcastnet"},
			{name: "steps", role: "forecast", model: "steps"},
		} {
			assetID := fmt.Sprintf("lead-010-threshold-%03d-%s", threshold, identity.name)
			objectPath := "layers/" + assetID + ".png"
			if err := os.WriteFile(filepath.Join(issueDirectory, filepath.FromSlash(objectPath)), png, 0o644); err != nil {
				t.Fatalf("write probability PNG fixture: %v", err)
			}
			layers = append(layers, map[string]any{
				"asset_id": assetID, "role": identity.role, "model": identity.model,
				"lead_minutes": 10, "threshold_mm_h": threshold,
				"valid_time_utc": "2021-08-10T17:10:00Z", "object_path": objectPath,
				"media_type": "image/png", "sha256": digest, "size_bytes": len(png),
				"width": 1, "height": 1, "valid_cell_count": 1,
				"no_event_cell_count": 1, "event_cell_count": 0, "missing_cell_count": 0,
			})
		}
	}
	manifest := map[string]any{
		"contract_version": "1.0", "renderer_version": "probability-renderer-v1",
		"render_profile_version": "probability-map-v1", "palette_version": "probability-v1",
		"verification_profile_version": profileVersion, "case_id": "midwest_case",
		"issue_key": "20210810T170000Z", "issue_time_utc": "2021-08-10T17:00:00Z",
		"truth_kind":           "observed_rate",
		"calibration_status":   "raw_ensemble_relative_frequency_uncalibrated",
		"operational_eligible": false, "product_publication_enabled": false,
		"grid": map[string]any{
			"grid_id": "grid", "grid_config_version": "grid-v1", "projection": "EPSG:4326",
			"fit_bounds":        []float64{-95, 39, -90, 41},
			"pixel_edge_bounds": []float64{-95.005, 38.995, -89.995, 41.005},
			"width":             1, "height": 1,
		},
		"palette": map[string]any{
			"valid_no_event_color": "#dce6e2",
			"stops": []map[string]any{
				{"minimum": 0.1, "color": "#bfe9ec"},
				{"minimum": 100, "color": "#b31945"},
			},
		},
		"lead_minutes": []int{10}, "thresholds_mm_h": []int{1, 5, 10, 20, 50},
		"layers": layers,
	}
	encodedManifest, err := json.Marshal(manifest)
	if err != nil {
		t.Fatalf("encode probability map manifest fixture: %v", err)
	}
	if err := os.WriteFile(filepath.Join(issueDirectory, "manifest.json"), encodedManifest, 0o644); err != nil {
		t.Fatalf("write probability map manifest fixture: %v", err)
	}
	index := map[string]any{
		"contract_version": "1.0", "verification_profile_version": profileVersion,
		"renderer_version": "probability-renderer-v1", "bundle_count": 1,
		"layer_count": 15, "issues": []map[string]any{{
			"case_id": "midwest_case", "case_category": "wet",
			"issue_time_utc": "2021-08-10T17:00:00Z", "issue_key": "20210810T170000Z",
			"manifest_path": "midwest_case/20210810T170000Z/manifest.json", "layer_count": 15,
		}},
	}
	encodedIndex, err := json.Marshal(index)
	if err != nil {
		t.Fatalf("encode probability map index fixture: %v", err)
	}
	if err := os.WriteFile(filepath.Join(directory, "probability-maps", "index.json"), encodedIndex, 0o644); err != nil {
		t.Fatalf("write probability map index fixture: %v", err)
	}
	return png
}

func mustReadFile(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read fixture %s: %v", path, err)
	}
	return data
}

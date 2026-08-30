package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

func TestPipelinePlannerIsDisabledByDefault(t *testing.T) {
	t.Setenv("RAINPULSE_PIPELINE_ENABLED", "false")
	settings, err := pipelineSettingsFromEnvironment()
	if err != nil || settings != nil {
		t.Fatalf("disabled planner settings = %#v, err=%v", settings, err)
	}
}

func TestPipelineSettingsRejectMixedGridConfigurations(t *testing.T) {
	root := t.TempDir()
	grid := "fuzhou-grid"
	version := "grid-v1"
	write := func(name, value string) string {
		t.Helper()
		path := filepath.Join(root, name+".yaml")
		if err := os.WriteFile(path, []byte(value), 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	gridPath := write("grid", "grid_id: "+grid+"\ngrid_config_version: "+version+"\n")
	qcPath := write("qc", "profile_version: qc-v1\npipeline_version: qc-pipeline-v1\n")
	mosaicPath := write("mosaic", "grid_id: "+grid+"\ngrid_config_version: "+version+"\nalignment:\n  expected_radar_ids: []\n")
	qpePath := write("qpe", "grid_id: "+grid+"\ngrid_config_version: "+version+"\n")
	nowcastPath := write("nowcast", "grid_id: "+grid+"\ngrid_config_version: "+version+"\nsequence:\n  minimum_frames: 3\n  maximum_frames: 6\n  timestep_minutes: 5\n")
	pystepsPath := write("pysteps", "grid_id: "+grid+"\ngrid_config_version: "+version+"\nextrapolation:\n  lead_count: 24\n  lead_step_minutes: 5\n")
	productPath := write("product", "grid_id: other-grid\ngrid_config_version: "+version+"\n")
	verificationPath := write("verification", `schema_version: "1.0"
profile_version: rp031-test
lifecycle: automatic_verification
forecast_contract_version: "1.1"
truth_contract_version: "1.2"
result_contract_version: "1.0"
lead_minutes: [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 105, 110, 115, 120]
models: [lk, persistence, translation]
thresholds_mm_h: [0.1, 1, 5]
fss_windows_km: [1, 5, 10]
accumulation_windows_minutes: [60, 120]
accumulation_thresholds_mm: [1, 5]
validity_domain: common
promotion_eligible: false
`)
	diagnosticPath := write("diagnostic", "profile_version: diagnostic-v1\nrenderer_version: renderer-v1\n")

	t.Setenv("RAINPULSE_PIPELINE_ENABLED", "true")
	t.Setenv("RAINPULSE_PIPELINE_RADAR_IDS", "z9598")
	for name, path := range map[string]string{
		"RAINPULSE_PIPELINE_QC_CONFIG":            qcPath,
		"RAINPULSE_PIPELINE_GRID_CONFIG":          gridPath,
		"RAINPULSE_PIPELINE_MOSAIC_CONFIG":        mosaicPath,
		"RAINPULSE_PIPELINE_QPE_CONFIG":           qpePath,
		"RAINPULSE_PIPELINE_DIAGNOSTIC_CONFIG":    diagnosticPath,
		"RAINPULSE_PIPELINE_NOWCAST_INPUT_CONFIG": nowcastPath,
		"RAINPULSE_PIPELINE_PYSTEPS_CONFIG":       pystepsPath,
		"RAINPULSE_PIPELINE_PRODUCT_CONFIG":       productPath,
		"RAINPULSE_PIPELINE_VERIFICATION_CONFIG":  verificationPath,
	} {
		t.Setenv(name, path)
	}

	_, err := pipelineSettingsFromEnvironment()
	if err == nil || !strings.Contains(err.Error(), "product config uses a different grid") {
		t.Fatalf("pipeline settings error = %v", err)
	}
	if err := os.WriteFile(
		productPath,
		[]byte("grid_id: "+grid+"\ngrid_config_version: "+version+"\n"),
		0o600,
	); err != nil {
		t.Fatal(err)
	}
	settings, err := pipelineSettingsFromEnvironment()
	if err != nil || settings == nil || settings.gridID != grid {
		t.Fatalf("valid pipeline settings = %#v, err=%v", settings, err)
	}
	if !settings.forecastEnabled || settings.requireAllRadars {
		t.Fatalf("unexpected operational planner defaults: %#v", settings)
	}
	t.Setenv("RAINPULSE_PIPELINE_FORECAST_ENABLED", "false")
	t.Setenv("RAINPULSE_PIPELINE_REQUIRE_ALL_RADARS", "true")
	settings, err = pipelineSettingsFromEnvironment()
	if err != nil || settings == nil || settings.forecastEnabled || !settings.requireAllRadars {
		t.Fatalf("historical analysis-only planner settings = %#v, err=%v", settings, err)
	}
}

func TestClosestScanByRadarSelectsOneCandidatePerRadar(t *testing.T) {
	analysisTime := time.Date(2026, 8, 29, 3, 5, 0, 0, time.UTC)
	farID := uuid.New()
	nearID := uuid.New()
	otherID := uuid.New()
	selected := closestScanByRadar([]workflow.RadarScan{
		{ID: farID, RadarID: "radar-a", VolumeEndTime: analysisTime.Add(-2 * time.Minute)},
		{ID: nearID, RadarID: "radar-a", VolumeEndTime: analysisTime.Add(-20 * time.Second)},
		{ID: otherID, RadarID: "radar-b", VolumeEndTime: analysisTime.Add(time.Minute)},
	}, analysisTime)

	if len(selected) != 2 || selected[0].RadarID != "radar-a" || selected[0].ID != nearID ||
		selected[1].RadarID != "radar-b" || selected[1].ID != otherID {
		t.Fatalf("unexpected mosaic candidates: %#v", selected)
	}
}

package controlplane

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
)

func TestPlanningWindowsDoNotReadBetweenLiveAndReplay(t *testing.T) {
	now := time.Now().UTC()
	start := now.Add(-30 * 24 * time.Hour)
	end := start.Add(time.Hour)
	p := &pipelinePlanner{settings: pipelineSettings{lookback: time.Hour, maximumMosaicOffset: 5 * time.Minute, historicalReplayStart: &start, historicalReplayEnd: &end}}
	windows, err := p.planningWindows(false)
	if err != nil || len(windows) != 2 {
		t.Fatalf("windows=%v err=%v", windows, err)
	}
	if !windows[0].Start.Equal(start) || !windows[0].End.Equal(end) {
		t.Fatal("explicit replay window drift")
	}
	verification, err := p.planningWindows(true)
	if err != nil || len(verification) != 1 {
		t.Fatalf("verification=%v err=%v", verification, err)
	}
	if verification[0].Start.Before(now.Add(-7 * time.Hour)) {
		t.Fatal("verification inherited historical replay")
	}
	p.settings.lookback = 0
	if _, err := p.planningWindows(false); err == nil {
		t.Fatal("unbounded lookback admitted")
	}
}

func TestRegenerationWindowIncludesBothOffsetEdges(t *testing.T) {
	at := time.Date(2026, 8, 28, 0, 0, 0, 0, time.UTC)
	p := &pipelinePlanner{settings: pipelineSettings{maximumMosaicOffset: 5 * time.Minute}}
	request := workflow.PipelineRegeneration{Frames: []workflow.PipelineRegenerationFrame{{AnalysisTime: at}, {AnalysisTime: at.Add(6 * time.Minute)}}}
	windows, err := p.regenerationScanWindow(request)
	if err != nil || len(windows) != 1 {
		t.Fatalf("windows=%v err=%v", windows, err)
	}
	if !windows[0].Start.Equal(at.Add(-5*time.Minute)) || !windows[0].End.After(at.Add(11*time.Minute)) {
		t.Fatal("inclusive source boundary was lost")
	}
	if _, err := p.regenerationScanWindow(workflow.PipelineRegeneration{}); err == nil {
		t.Fatal("empty request admitted")
	}
}

func TestPlanningPauseAndConfigurationDrift(t *testing.T) {
	root := t.TempDir()
	gate := filepath.Join(root, "release-gate.json")
	t.Setenv("RAINPULSE_RELEASE_GATE_FILE", gate)
	config := filepath.Join(root, "qc.yaml")
	if err := os.WriteFile(config, []byte("profile_version: before\n"), 0600); err != nil {
		t.Fatal(err)
	}
	p := &pipelinePlanner{settings: pipelineSettings{qcConfig: config, mode: "realtime_shadow"}}
	release, err := p.beginPlanning(context.Background())
	if err != nil || release == nil {
		t.Fatalf("release=%v err=%v", release != nil, err)
	}
	release()
	if err := os.WriteFile(gate, []byte(`{"schema_version":1,"state":"paused","release_id":"test"}`), 0600); err != nil {
		t.Fatal(err)
	}
	release, err = p.beginPlanning(context.Background())
	if err != nil || release != nil {
		t.Fatalf("paused admission err=%v", err)
	}
	if err := os.WriteFile(config, []byte("profile_version: after\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = p.beginPlanning(context.Background()); err == nil {
		t.Fatal("config drift admitted")
	}
	raw, err := os.ReadFile(filepath.Join(root, "planner-release.json"))
	if err != nil {
		t.Fatal(err)
	}
	var evidence map[string]any
	if err = json.Unmarshal(raw, &evidence); err != nil {
		t.Fatal(err)
	}
	if evidence["current_qc_sha256"] == evidence["loaded_qc_sha256"] {
		t.Fatal("config drift hidden")
	}
}

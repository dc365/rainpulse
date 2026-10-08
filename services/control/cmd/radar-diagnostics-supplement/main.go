// radar-diagnostics-supplement submits native single-station diagnostic layers
// through the regular Go workflow transaction and outbox.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/bdpruntime"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/releaseguard"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/runtimeconfig"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"gopkg.in/yaml.v3"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	if len(os.Args) != 5 {
		return fmt.Errorf("usage: radar-diagnostics-supplement ANALYSIS_UUID CONFIG_YAML S_RADAR_ID REVISION_UUID")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	if _, err := bdpruntime.Prepare(bdpruntime.ComponentOrchestrator, true); err != nil {
		return err
	}
	release, err := releaseguard.Acquire(ctx)
	if err != nil {
		return err
	}
	defer release()
	analysisID, err := uuid.Parse(os.Args[1])
	if err != nil {
		return err
	}
	revision, err := uuid.Parse(os.Args[4])
	if err != nil || revision == uuid.Nil {
		return fmt.Errorf("nonzero immutable revision UUID required")
	}
	db, err := runtimeconfig.DatabaseURL()
	if err != nil {
		return err
	}
	pool, err := pgxpool.New(ctx, db)
	if err != nil {
		return err
	}
	defer pool.Close()
	store := postgres.New(pool)
	analysis, err := store.GetAnalysisCycle(ctx, analysisID)
	if err != nil {
		return err
	}
	if analysis.AnalysisURI == nil || analysis.Status != workflow.AnalysisReady {
		return fmt.Errorf("analysis is not ready")
	}
	var inputs []workflow.AnalysisDiagnosticRadarInput
	for _, radar := range analysis.Radars {
		if radar.RadarID == os.Args[3] {
			return fmt.Errorf("station already participates; use regular analysis-diagnostics")
		}
		if radar.State != workflow.AnalysisRadarParticipating || radar.ScanID == nil {
			continue
		}
		scan, err := store.GetRadarScan(ctx, *radar.ScanID)
		if err != nil {
			return err
		}
		if scan.RadarID != radar.RadarID || scan.QCURI == nil {
			return fmt.Errorf("missing exact contributor native QC")
		}
		inputs = append(inputs, workflow.AnalysisDiagnosticRadarInput{RadarID: radar.RadarID, ScanID: *radar.ScanID, QCURI: *scan.QCURI})
	}
	supplement := workflow.AnalysisDiagnosticRadarInput{RadarID: os.Args[3]}
	err = pool.QueryRow(ctx, `SELECT rs.scan_id,rs.qc_uri FROM radar_scan_runs rs JOIN radar_scans s USING(scan_id)
 WHERE rs.radar_id=$1 AND NULLIF(rs.qc_uri,'') IS NOT NULL AND s.volume_end_time<=$2
 AND s.volume_end_time >= $2::timestamptz-interval '720 seconds'
 ORDER BY s.volume_end_time DESC,rs.scan_id DESC LIMIT 1`, supplement.RadarID, analysis.AnalysisTime).Scan(&supplement.ScanID, &supplement.QCURI)
	if err != nil {
		return fmt.Errorf("no causal supplemental QC: %w", err)
	}
	raw, err := os.ReadFile(os.Args[2])
	if err != nil {
		return err
	}
	var conf struct {
		Profile  string `yaml:"profile_version"`
		Renderer string `yaml:"renderer_version"`
		Flags    string `yaml:"flag_definition_version"`
	}
	if err = yaml.Unmarshal(raw, &conf); err != nil {
		return err
	}
	var value map[string]any
	if err = yaml.Unmarshal(raw, &value); err != nil {
		return err
	}
	configJSON, err := json.Marshal(value)
	if err != nil {
		return err
	}
	hash := sha256.Sum256(raw)
	job, err := orchestration.NewService(store, orchestration.Options{}).CreateAnalysisDiagnostics(ctx, orchestration.AnalysisDiagnosticsInput{
		AnalysisID: analysis.ID, RunID: analysis.RunID, AnalysisTime: analysis.AnalysisTime, GridID: analysis.GridID, AnalysisURI: *analysis.AnalysisURI, CurrentStatus: analysis.Status,
		RadarInputs: inputs, SupplementalRadarInputs: []workflow.AnalysisDiagnosticRadarInput{supplement}, SupplementalRevisionID: revision,
		DiagnosticConfig: configJSON, DiagnosticConfigSHA256: fmt.Sprintf("%x", hash), DiagnosticConfigVersion: conf.Profile, RendererVersion: conf.Renderer, FlagDefinitionVersion: conf.Flags,
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"analysis_id": analysis.ID, "job_id": job.ID, "run_id": job.RunID, "supplement_scan_id": supplement.ScanID, "supplement_qc_uri": supplement.QCURI, "issue_time": analysis.AnalysisTime})
}

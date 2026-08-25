package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/messaging"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/runtimeconfig"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"gopkg.in/yaml.v3"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	pool, store, bus, service, err := dependencies(ctx)
	if err != nil {
		slog.Error("initialize RainPulse orchestrator", "error", err)
		os.Exit(1)
	}
	defer pool.Close()
	defer bus.Close()

	command := "serve"
	if len(os.Args) > 1 {
		command = os.Args[1]
	}
	switch command {
	case "serve":
		if err := serve(ctx, store, bus, service); err != nil && !errors.Is(err, context.Canceled) {
			slog.Error("RainPulse orchestrator stopped", "error", err)
			os.Exit(1)
		}
	case "simulate":
		if err := simulate(ctx, service, false); err != nil {
			slog.Error("create simulated run", "error", err)
			os.Exit(1)
		}
	case "simulate-failure":
		if err := simulate(ctx, service, true); err != nil {
			slog.Error("create simulated failure run", "error", err)
			os.Exit(1)
		}
	case "simulate-workflows":
		if err := simulateWorkflows(ctx, service); err != nil {
			slog.Error("create simulated radar/analysis workflows", "error", err)
			os.Exit(1)
		}
	case "radar-decode":
		if len(os.Args) != 6 {
			slog.Error("radar-decode requires config YAML, input path, volume start and volume end")
			os.Exit(2)
		}
		if err := radarDecode(ctx, service, os.Args[2], os.Args[3], os.Args[4], os.Args[5]); err != nil {
			slog.Error("create radar decode workflow", "error", err)
			os.Exit(1)
		}
	case "radar-qc":
		if len(os.Args) != 4 {
			slog.Error("radar-qc requires a scan UUID and QC config YAML")
			os.Exit(2)
		}
		if err := radarQC(ctx, store, service, os.Args[2], os.Args[3]); err != nil {
			slog.Error("create radar QC workflow", "error", err)
			os.Exit(1)
		}
	case "radar-grid":
		if len(os.Args) != 4 {
			slog.Error("radar-grid requires a scan UUID and grid profile YAML")
			os.Exit(2)
		}
		if err := radarGrid(ctx, store, service, os.Args[2], os.Args[3]); err != nil {
			slog.Error("create radar grid workflow", "error", err)
			os.Exit(1)
		}
	case "analysis-mosaic":
		if len(os.Args) < 5 {
			slog.Error("analysis-mosaic requires analysis time, mosaic config YAML and one or more scan UUIDs")
			os.Exit(2)
		}
		if err := analysisMosaic(ctx, store, service, os.Args[2], os.Args[3], os.Args[4:]); err != nil {
			slog.Error("create analysis mosaic workflow", "error", err)
			os.Exit(1)
		}
	case "analysis-qpe":
		if len(os.Args) != 4 {
			slog.Error("analysis-qpe requires an analysis UUID and QPE config YAML")
			os.Exit(2)
		}
		if err := analysisQPE(ctx, store, service, os.Args[2], os.Args[3]); err != nil {
			slog.Error("create analysis QPE workflow", "error", err)
			os.Exit(1)
		}
	case "analysis-diagnostics":
		if len(os.Args) != 4 {
			slog.Error("analysis-diagnostics requires an analysis UUID and diagnostic config YAML")
			os.Exit(2)
		}
		if err := analysisDiagnostics(ctx, store, service, os.Args[2], os.Args[3]); err != nil {
			slog.Error("create analysis diagnostic workflow", "error", err)
			os.Exit(1)
		}
	case "nowcast-input":
		if len(os.Args) != 4 {
			slog.Error("nowcast-input requires an RFC3339 issue time and NowcastInput config YAML")
			os.Exit(2)
		}
		if err := nowcastInput(ctx, store, service, os.Args[2], os.Args[3]); err != nil {
			slog.Error("create NowcastInput workflow", "error", err)
			os.Exit(1)
		}
	case "pysteps-lk":
		if len(os.Args) != 4 {
			slog.Error("pysteps-lk requires a forecast run UUID and pySTEPS-LK config YAML")
			os.Exit(2)
		}
		if err := pystepsLK(ctx, store, service, os.Args[2], os.Args[3]); err != nil {
			slog.Error("create pySTEPS-LK workflow", "error", err)
			os.Exit(1)
		}
	case "product-build":
		if len(os.Args) != 4 {
			slog.Error("product-build requires a forecast run UUID and product config YAML")
			os.Exit(2)
		}
		if err := productBuild(ctx, store, service, os.Args[2], os.Args[3]); err != nil {
			slog.Error("create application product workflow", "error", err)
			os.Exit(1)
		}
	case "complete":
		if len(os.Args) != 3 {
			slog.Error("complete requires a job UUID")
			os.Exit(2)
		}
		if err := complete(ctx, store, bus, service, os.Args[2]); err != nil {
			slog.Error("publish simulated completion", "error", err)
			os.Exit(1)
		}
	case "replay":
		if len(os.Args) != 3 {
			slog.Error("replay requires a job UUID")
			os.Exit(2)
		}
		if err := replay(ctx, store, bus, os.Args[2]); err != nil {
			slog.Error("replay job request", "error", err)
			os.Exit(1)
		}
	default:
		slog.Error("unknown orchestrator command", "command", command)
		os.Exit(2)
	}
}

type radarConfiguration struct {
	ConfigVersion string `yaml:"config_version"`
	RadarID       string `yaml:"radar_id"`
	Lifecycle     string `yaml:"lifecycle"`
	DisplayName   string `yaml:"display_name"`
	Source        struct {
		Format string `yaml:"format"`
	} `yaml:"source"`
}

type qcConfiguration struct {
	ProfileVersion        string `yaml:"profile_version"`
	PipelineVersion       string `yaml:"pipeline_version"`
	FlagDefinitionVersion string `yaml:"flag_definition_version"`
}

type gridConfiguration struct {
	ProfileVersion    string `yaml:"profile_version"`
	AlgorithmVersion  string `yaml:"algorithm_version"`
	GridID            string `yaml:"grid_id"`
	GridConfigVersion string `yaml:"grid_config_version"`
}

type mosaicConfiguration struct {
	ProfileVersion        string `yaml:"profile_version"`
	AlgorithmVersion      string `yaml:"algorithm_version"`
	FlagDefinitionVersion string `yaml:"flag_definition_version"`
	GridID                string `yaml:"grid_id"`
	GridConfigVersion     string `yaml:"grid_config_version"`
	Alignment             struct {
		MaximumAbsoluteOffsetSeconds int      `yaml:"maximum_absolute_offset_seconds"`
		MinimumContributors          int      `yaml:"minimum_contributors"`
		ExpectedRadarIDs             []string `yaml:"expected_radar_ids"`
	} `yaml:"alignment"`
}

type qpeConfiguration struct {
	ProfileVersion        string `yaml:"profile_version"`
	AlgorithmVersion      string `yaml:"algorithm_version"`
	FlagDefinitionVersion string `yaml:"flag_definition_version"`
	GridID                string `yaml:"grid_id"`
	GridConfigVersion     string `yaml:"grid_config_version"`
}

type diagnosticConfiguration struct {
	ProfileVersion        string `yaml:"profile_version"`
	RendererVersion       string `yaml:"renderer_version"`
	FlagDefinitionVersion string `yaml:"flag_definition_version"`
}

type nowcastInputConfiguration struct {
	ProfileVersion    string `yaml:"profile_version"`
	BuilderVersion    string `yaml:"builder_version"`
	GridID            string `yaml:"grid_id"`
	GridConfigVersion string `yaml:"grid_config_version"`
	Sequence          struct {
		MinimumFrames   int    `yaml:"minimum_frames"`
		MaximumFrames   int    `yaml:"maximum_frames"`
		TimestepMinutes int    `yaml:"timestep_minutes"`
		Selection       string `yaml:"selection"`
	} `yaml:"sequence"`
	Gates struct {
		MinimumValidCoverageRatio float64 `yaml:"minimum_valid_coverage_ratio"`
		MinimumMeanQualityIndex   float64 `yaml:"minimum_mean_quality_index"`
	} `yaml:"gates"`
}

type pystepsLKConfiguration struct {
	ProfileVersion                string `yaml:"profile_version"`
	ModelID                       string `yaml:"model_id"`
	ModelVersion                  string `yaml:"model_version"`
	ForecastOutputContractVersion string `yaml:"forecast_output_contract_version"`
	GridID                        string `yaml:"grid_id"`
	GridConfigVersion             string `yaml:"grid_config_version"`
	Extrapolation                 struct {
		LeadCount       int      `yaml:"lead_count"`
		LeadStepMinutes int      `yaml:"lead_step_minutes"`
		Baselines       []string `yaml:"baselines"`
	} `yaml:"extrapolation"`
}

type productConfiguration struct {
	ProfileVersion                string `yaml:"profile_version"`
	BundleContractVersion         string `yaml:"bundle_contract_version"`
	ForecastOutputContractVersion string `yaml:"forecast_output_contract_version"`
	GridID                        string `yaml:"grid_id"`
	GridConfigVersion             string `yaml:"grid_config_version"`
}

func radarDecode(
	ctx context.Context,
	service *orchestration.Service,
	configPath string,
	inputPath string,
	rawStart string,
	rawEnd string,
) error {
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read radar configuration: %w", err)
	}
	var config radarConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode radar configuration: %w", err)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize radar configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode radar configuration: %w", err)
	}
	absoluteInput, err := filepath.Abs(inputPath)
	if err != nil {
		return fmt.Errorf("resolve radar input path: %w", err)
	}
	info, err := os.Stat(absoluteInput)
	if err != nil {
		return fmt.Errorf("stat radar input: %w", err)
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("radar input is not a regular file")
	}
	inputHash, err := sha256File(absoluteInput)
	if err != nil {
		return err
	}
	start, err := time.Parse(time.RFC3339Nano, rawStart)
	if err != nil {
		return fmt.Errorf("parse volume start: %w", err)
	}
	end, err := time.Parse(time.RFC3339Nano, rawEnd)
	if err != nil {
		return fmt.Errorf("parse volume end: %w", err)
	}
	configHash := sha256.Sum256(configBytes)
	displayName := config.DisplayName
	scan, job, err := service.CreateRadarDecode(ctx, orchestration.RadarDecodeInput{
		RadarID: config.RadarID, DisplayName: &displayName,
		Lifecycle:     workflow.RadarLifecycle(config.Lifecycle),
		ConfigVersion: config.ConfigVersion, Config: configJSON,
		ConfigSHA256: fmt.Sprintf("%x", configHash), SourceFormat: config.Source.Format,
		InputURI:    (&url.URL{Scheme: "file", Path: absoluteInput}).String(),
		InputSHA256: inputHash, InputSizeBytes: info.Size(),
		VolumeStartTime: start, VolumeEndTime: end,
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"scan_id": scan.ID.String(), "run_id": scan.RunID.String(), "job_id": job.ID.String(),
	})
}

func sha256File(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", fmt.Errorf("open radar input: %w", err)
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", fmt.Errorf("hash radar input: %w", err)
	}
	return fmt.Sprintf("%x", hash.Sum(nil)), nil
}

func radarQC(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawScanID string,
	configPath string,
) error {
	scanID, err := uuid.Parse(rawScanID)
	if err != nil {
		return fmt.Errorf("parse radar scan UUID: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read radar QC configuration: %w", err)
	}
	var config qcConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode radar QC configuration: %w", err)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize radar QC configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode radar QC configuration: %w", err)
	}
	configHash := sha256.Sum256(configBytes)
	scan, err := store.GetRadarScan(ctx, scanID)
	if err != nil {
		return err
	}
	if scan.NormalizedURI == nil {
		return fmt.Errorf("radar scan has no normalized volume")
	}
	health, err := store.GetRadarHealthMetrics(ctx, scanID)
	if err != nil {
		return err
	}
	job, err := service.CreateRadarQC(ctx, orchestration.RadarQCInput{
		ScanID: scan.ID, RunID: scan.RunID, RadarID: scan.RadarID,
		RadarConfigVersion:    scan.RadarConfigVersion,
		NormalizedURI:         *scan.NormalizedURI,
		CurrentStatus:         scan.Status,
		Health:                health.Health,
		QCProfile:             config.ProfileVersion,
		QCPipelineVersion:     config.PipelineVersion,
		FlagDefinitionVersion: config.FlagDefinitionVersion,
		QCConfig:              configJSON,
		QCConfigSHA256:        fmt.Sprintf("%x", configHash),
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"scan_id": scan.ID.String(), "run_id": scan.RunID.String(), "job_id": job.ID.String(),
	})
}

func radarGrid(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawScanID string,
	configPath string,
) error {
	scanID, err := uuid.Parse(rawScanID)
	if err != nil {
		return fmt.Errorf("parse radar scan UUID: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read radar grid configuration: %w", err)
	}
	var config gridConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode radar grid configuration: %w", err)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize radar grid configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode radar grid configuration: %w", err)
	}
	configHash := sha256.Sum256(configBytes)
	scan, err := store.GetRadarScan(ctx, scanID)
	if err != nil {
		return err
	}
	if scan.QCURI == nil {
		return fmt.Errorf("radar scan has no QC volume")
	}
	job, err := service.CreateRadarGrid(ctx, orchestration.RadarGridInput{
		ScanID: scan.ID, RunID: scan.RunID, RadarID: scan.RadarID,
		QCURI: *scan.QCURI, CurrentStatus: scan.Status,
		GridID: config.GridID, GridConfigVersion: config.GridConfigVersion,
		GridProfileVersion: config.ProfileVersion,
		HybridScanVersion:  config.AlgorithmVersion,
		GridConfig:         configJSON, GridConfigSHA256: fmt.Sprintf("%x", configHash),
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"scan_id": scan.ID.String(), "run_id": scan.RunID.String(), "job_id": job.ID.String(),
	})
}

func analysisMosaic(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawAnalysisTime string,
	configPath string,
	rawScanIDs []string,
) error {
	analysisTime, err := time.Parse(time.RFC3339Nano, rawAnalysisTime)
	if err != nil {
		return fmt.Errorf("parse analysis time: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read radar mosaic configuration: %w", err)
	}
	var config mosaicConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode radar mosaic configuration: %w", err)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize radar mosaic configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode radar mosaic configuration: %w", err)
	}
	candidates := make([]orchestration.AnalysisMosaicCandidate, 0, len(rawScanIDs))
	for _, rawScanID := range rawScanIDs {
		scanID, err := uuid.Parse(rawScanID)
		if err != nil {
			return fmt.Errorf("parse radar scan UUID %q: %w", rawScanID, err)
		}
		scan, err := store.GetRadarScan(ctx, scanID)
		if err != nil {
			return err
		}
		if scan.GridURI == nil {
			return fmt.Errorf("radar scan %s has no committed RadarGrid", scan.ID)
		}
		gridMetrics, err := store.GetRadarGridMetrics(ctx, scanID)
		if err != nil {
			return err
		}
		if gridMetrics.GridID != config.GridID ||
			gridMetrics.GridConfigVersion != config.GridConfigVersion {
			return fmt.Errorf("radar scan %s uses a different target grid", scan.ID)
		}
		candidates = append(candidates, orchestration.AnalysisMosaicCandidate{
			RadarID: scan.RadarID, ScanID: scan.ID, GridURI: *scan.GridURI,
			VolumeEndTime: scan.VolumeEndTime, CurrentStatus: scan.Status,
			HybridScanVersion: gridMetrics.AlgorithmVersion,
		})
	}
	configHash := sha256.Sum256(configBytes)
	analysis, job, err := service.CreateAnalysisMosaic(ctx, orchestration.AnalysisMosaicInput{
		AnalysisTime: analysisTime, GridID: config.GridID,
		GridConfigVersion:      config.GridConfigVersion,
		MosaicConfigVersion:    config.ProfileVersion,
		MosaicAlgorithmVersion: config.AlgorithmVersion,
		FlagDefinitionVersion:  config.FlagDefinitionVersion,
		MaximumAbsoluteOffset: time.Duration(
			config.Alignment.MaximumAbsoluteOffsetSeconds,
		) * time.Second,
		MinimumContributors: config.Alignment.MinimumContributors,
		ExpectedRadarIDs:    config.Alignment.ExpectedRadarIDs,
		Candidates:          candidates, MosaicConfig: configJSON,
		MosaicConfigSHA256: fmt.Sprintf("%x", configHash),
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"analysis_id": analysis.ID.String(),
		"run_id":      analysis.RunID.String(),
		"job_id":      job.ID.String(),
	})
}

func analysisQPE(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawAnalysisID string,
	configPath string,
) error {
	analysisID, err := uuid.Parse(rawAnalysisID)
	if err != nil {
		return fmt.Errorf("parse analysis UUID: %w", err)
	}
	analysis, err := store.GetAnalysisCycle(ctx, analysisID)
	if err != nil {
		return err
	}
	if analysis.MosaicURI == nil {
		return fmt.Errorf("analysis %s has no committed RadarMosaic", analysis.ID)
	}
	mosaicMetrics, err := store.GetAnalysisMosaicMetrics(ctx, analysisID)
	if err != nil {
		return err
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read QPE configuration: %w", err)
	}
	var config qpeConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode QPE configuration: %w", err)
	}
	if config.GridID != analysis.GridID ||
		config.GridConfigVersion != mosaicMetrics.GridConfigVersion {
		return fmt.Errorf("QPE configuration uses a different target grid")
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize QPE configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode QPE configuration: %w", err)
	}
	configHash := sha256.Sum256(configBytes)
	job, err := service.CreateAnalysisQPE(ctx, orchestration.AnalysisQPEInput{
		AnalysisID: analysis.ID, RunID: analysis.RunID,
		AnalysisTime: analysis.AnalysisTime, GridID: analysis.GridID,
		GridConfigVersion:      mosaicMetrics.GridConfigVersion,
		MosaicConfigVersion:    mosaicMetrics.ProfileVersion,
		MosaicAlgorithmVersion: mosaicMetrics.AlgorithmVersion,
		FlagDefinitionVersion:  config.FlagDefinitionVersion,
		MosaicURI:              *analysis.MosaicURI, CurrentStatus: analysis.Status,
		QPEConfigVersion:    config.ProfileVersion,
		QPEAlgorithmVersion: config.AlgorithmVersion,
		QPEConfig:           configJSON, QPEConfigSHA256: fmt.Sprintf("%x", configHash),
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"analysis_id": analysis.ID.String(),
		"run_id":      analysis.RunID.String(),
		"job_id":      job.ID.String(),
	})
}

func analysisDiagnostics(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawAnalysisID string,
	configPath string,
) error {
	analysisID, err := uuid.Parse(rawAnalysisID)
	if err != nil {
		return fmt.Errorf("parse analysis UUID: %w", err)
	}
	analysis, err := store.GetAnalysisCycle(ctx, analysisID)
	if err != nil {
		return err
	}
	if analysis.AnalysisURI == nil {
		return fmt.Errorf("analysis %s has no committed RadarAnalysis", analysis.ID)
	}
	inputs := make([]workflow.AnalysisDiagnosticRadarInput, 0, len(analysis.Radars))
	for _, radar := range analysis.Radars {
		if radar.State != workflow.AnalysisRadarParticipating || radar.ScanID == nil {
			continue
		}
		scan, scanErr := store.GetRadarScan(ctx, *radar.ScanID)
		if scanErr != nil {
			return scanErr
		}
		if scan.RadarID != radar.RadarID || scan.QCURI == nil {
			return fmt.Errorf("analysis contributor %s has no exact QCRadarVolume", radar.RadarID)
		}
		inputs = append(inputs, workflow.AnalysisDiagnosticRadarInput{
			RadarID: radar.RadarID,
			ScanID:  *radar.ScanID,
			QCURI:   *scan.QCURI,
		})
	}
	if len(inputs) == 0 {
		return fmt.Errorf("analysis %s has no participating QC radar inputs", analysis.ID)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read diagnostic configuration: %w", err)
	}
	var config diagnosticConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode diagnostic configuration: %w", err)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize diagnostic configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode diagnostic configuration: %w", err)
	}
	configHash := sha256.Sum256(configBytes)
	job, err := service.CreateAnalysisDiagnostics(ctx, orchestration.AnalysisDiagnosticsInput{
		AnalysisID:              analysis.ID,
		RunID:                   analysis.RunID,
		AnalysisTime:            analysis.AnalysisTime,
		GridID:                  analysis.GridID,
		AnalysisURI:             *analysis.AnalysisURI,
		CurrentStatus:           analysis.Status,
		RadarInputs:             inputs,
		DiagnosticConfig:        configJSON,
		DiagnosticConfigSHA256:  fmt.Sprintf("%x", configHash),
		DiagnosticConfigVersion: config.ProfileVersion,
		RendererVersion:         config.RendererVersion,
		FlagDefinitionVersion:   config.FlagDefinitionVersion,
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"analysis_id": analysis.ID.String(),
		"run_id":      analysis.RunID.String(),
		"job_id":      job.ID.String(),
	})
}

func nowcastInput(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawIssueTime string,
	configPath string,
) error {
	issueTime, err := time.Parse(time.RFC3339Nano, rawIssueTime)
	if err != nil {
		return fmt.Errorf("parse NowcastInput issue time: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read NowcastInput configuration: %w", err)
	}
	var config nowcastInputConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode NowcastInput configuration: %w", err)
	}
	if config.Sequence.Selection != "latest_contiguous" {
		return fmt.Errorf("unsupported NowcastInput frame selection %q", config.Sequence.Selection)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize NowcastInput configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode NowcastInput configuration: %w", err)
	}
	candidates, err := store.ListNowcastInputCandidates(
		ctx, issueTime, config.GridID, config.Sequence.MaximumFrames,
	)
	if err != nil {
		return err
	}
	configHash := sha256.Sum256(configBytes)
	run, job, err := service.CreateNowcastInput(ctx, orchestration.NowcastInputInput{
		IssueTime: issueTime, GridID: config.GridID,
		GridConfigVersion:         config.GridConfigVersion,
		PreprocessVersion:         config.BuilderVersion,
		GateConfigVersion:         config.ProfileVersion,
		MinimumFrames:             config.Sequence.MinimumFrames,
		MaximumFrames:             config.Sequence.MaximumFrames,
		Timestep:                  time.Duration(config.Sequence.TimestepMinutes) * time.Minute,
		MinimumValidCoverageRatio: config.Gates.MinimumValidCoverageRatio,
		MinimumMeanQualityIndex:   config.Gates.MinimumMeanQualityIndex,
		Candidates:                candidates, Config: configJSON,
		ConfigSHA256: fmt.Sprintf("%x", configHash),
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{
		"run_id": run.ID.String(), "job_id": job.ID.String(),
		"issue_time": run.IssueTime, "candidate_count": len(candidates),
	})
}

func pystepsLK(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawRunID string,
	configPath string,
) error {
	runID, err := uuid.Parse(rawRunID)
	if err != nil {
		return fmt.Errorf("parse pySTEPS-LK run UUID: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read pySTEPS-LK configuration: %w", err)
	}
	var config pystepsLKConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode pySTEPS-LK configuration: %w", err)
	}
	if config.GridConfigVersion == "" || config.Extrapolation.LeadCount != 24 ||
		config.Extrapolation.LeadStepMinutes != 5 {
		return fmt.Errorf("pySTEPS-LK configuration differs from RP-014")
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize pySTEPS-LK configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode pySTEPS-LK configuration: %w", err)
	}
	input, err := store.GetPystepsLKInput(ctx, runID)
	if err != nil {
		return err
	}
	if input.GridID != config.GridID {
		return fmt.Errorf("pySTEPS-LK grid differs from committed NowcastInput")
	}
	configHash := sha256.Sum256(configBytes)
	input.ModelID = config.ModelID
	input.ModelVersion = config.ModelVersion
	input.ConfigVersion = config.ProfileVersion
	input.ForecastContractVersion = config.ForecastOutputContractVersion
	input.BaselineModels = config.Extrapolation.Baselines
	input.Config = configJSON
	input.ConfigSHA256 = fmt.Sprintf("%x", configHash)
	run, job, err := service.CreatePystepsLK(ctx, input)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{
		"run_id": run.ID.String(), "job_id": job.ID.String(),
		"issue_time": run.IssueTime, "input_asset_count": len(input.InputAssetIDs),
	})
}

func productBuild(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawRunID string,
	configPath string,
) error {
	runID, err := uuid.Parse(rawRunID)
	if err != nil {
		return fmt.Errorf("parse product-build run UUID: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read product-build configuration: %w", err)
	}
	var config productConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode product-build configuration: %w", err)
	}
	if config.BundleContractVersion != "1.0" ||
		config.ForecastOutputContractVersion != "1.1" ||
		config.GridID == "" || config.GridConfigVersion == "" {
		return fmt.Errorf("product-build configuration differs from RP-015")
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize product-build configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode product-build configuration: %w", err)
	}
	input, err := store.GetProductBuildInput(ctx, runID)
	if err != nil {
		return err
	}
	if input.GridID != config.GridID {
		return fmt.Errorf("product-build grid differs from committed ForecastOutput")
	}
	configHash := sha256.Sum256(configBytes)
	input.ProductConfigVersion = config.ProfileVersion
	input.ProductBundleContract = config.BundleContractVersion
	input.ProductConfig = configJSON
	input.ProductConfigSHA256 = fmt.Sprintf("%x", configHash)
	run, job, err := service.CreateProductBuild(ctx, input)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{
		"run_id": run.ID.String(), "job_id": job.ID.String(),
		"issue_time": run.IssueTime, "forecast_uri": input.ForecastURI,
	})
}

func dependencies(ctx context.Context) (*pgxpool.Pool, *postgresstore.Store, *messaging.JetStream, *orchestration.Service, error) {
	databaseURL, err := runtimeconfig.DatabaseURL()
	if err != nil {
		return nil, nil, nil, nil, err
	}
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, nil, nil, nil, fmt.Errorf("open PostgreSQL pool: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, nil, nil, nil, fmt.Errorf("ping PostgreSQL: %w", err)
	}
	store := postgresstore.New(pool)
	bus, err := messaging.Connect(runtimeconfig.NATSURL(), "rainpulse-orchestrator")
	if err != nil {
		pool.Close()
		return nil, nil, nil, nil, err
	}
	if err := bus.Ensure(ctx); err != nil {
		bus.Close()
		pool.Close()
		return nil, nil, nil, nil, err
	}
	return pool, store, bus, orchestration.NewService(store, orchestration.Options{}), nil
}

func serve(ctx context.Context, store *postgresstore.Store, bus *messaging.JetStream, service *orchestration.Service) error {
	healthServer := &http.Server{
		Addr:              environmentOrDefault("RAINPULSE_ORCHESTRATOR_HEALTH_ADDR", ":8090"),
		ReadHeaderTimeout: 3 * time.Second,
		Handler: http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
			if request.URL.Path != "/healthz" {
				http.NotFound(response, request)
				return
			}
			checkCtx, cancel := context.WithTimeout(request.Context(), 2*time.Second)
			defer cancel()
			if !bus.Healthy() || store.Ping(checkCtx) != nil {
				http.Error(response, "unavailable", http.StatusServiceUnavailable)
				return
			}
			response.WriteHeader(http.StatusOK)
			_, _ = response.Write([]byte("ok\n"))
		}),
	}
	serverErrors := make(chan error, 1)
	go func() {
		if err := healthServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErrors <- err
		}
	}()
	go dispatchLoop(ctx, service, bus)
	consumerErrors := make(chan error, 1)
	go func() {
		consumerErrors <- bus.ConsumeResults(ctx, service.HandleResult)
	}()

	slog.Info("RainPulse orchestrator ready")
	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return healthServer.Shutdown(shutdownCtx)
	case err := <-serverErrors:
		return fmt.Errorf("orchestrator health server: %w", err)
	case err := <-consumerErrors:
		return err
	}
}

func dispatchLoop(ctx context.Context, service *orchestration.Service, publisher orchestration.Publisher) {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	for {
		dispatched, err := service.DispatchOnce(ctx, publisher)
		if err != nil {
			slog.Error("dispatch outbox event", "error", err)
		}
		if dispatched {
			continue
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func simulate(ctx context.Context, service *orchestration.Service, forceFailure bool) error {
	issueTime := time.Now().UTC().Truncate(5 * time.Minute)
	var run workflow.Run
	var job workflow.Job
	var err error
	if forceFailure {
		run, job, err = service.CreateFailureSimulation(ctx, issueTime)
	} else {
		run, job, err = service.CreateSimulation(ctx, issueTime)
	}
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"run_id": run.ID.String(),
		"job_id": job.ID.String(),
	})
}

func simulateWorkflows(ctx context.Context, service *orchestration.Service) error {
	simulation, err := service.CreateThreeWorkflowSimulation(ctx, time.Now().UTC())
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{
		"analysis_id":     simulation.Analysis.ID.String(),
		"analysis_run_id": simulation.Analysis.RunID.String(),
		"scan_ids": []string{
			simulation.Scans[0].ID.String(),
			simulation.Scans[1].ID.String(),
		},
	})
}

func complete(ctx context.Context, store *postgresstore.Store, bus *messaging.JetStream, service *orchestration.Service, rawJobID string) error {
	jobID, err := uuid.Parse(rawJobID)
	if err != nil {
		return fmt.Errorf("parse job UUID: %w", err)
	}
	job, err := store.GetJob(ctx, jobID)
	if err != nil {
		return err
	}
	event := service.BuildSimulationCompletion(job)
	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("encode completion event: %w", err)
	}
	return bus.Publish(ctx, workflow.OutboxEvent{
		ID:      event.EventID,
		Subject: orchestration.JobCompletedSubject,
		Payload: payload,
	})
}

func replay(ctx context.Context, store *postgresstore.Store, bus *messaging.JetStream, rawJobID string) error {
	jobID, err := uuid.Parse(rawJobID)
	if err != nil {
		return fmt.Errorf("parse job UUID: %w", err)
	}
	job, err := store.GetJob(ctx, jobID)
	if err != nil {
		return err
	}
	var event map[string]any
	if err := json.Unmarshal(job.RequestPayload, &event); err != nil {
		return fmt.Errorf("decode job request: %w", err)
	}
	eventType, _ := event["event_type"].(string)
	event["event_id"] = uuid.New().String()
	event["occurred_at"] = time.Now().UTC()
	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("encode replayed job request: %w", err)
	}
	subject := orchestration.JobRequestedSubject
	if eventType == orchestration.RadarDecodeRequestedEventType {
		subject = orchestration.RadarDecodeRequestedSubject
	} else if eventType == orchestration.RadarQCRequestedEventType {
		subject = orchestration.RadarQCRequestedSubject
	} else if eventType == orchestration.RadarGridRequestedEventType {
		subject = orchestration.RadarGridRequestedSubject
	} else if eventType == orchestration.AnalysisMosaicRequestedEventType {
		subject = orchestration.AnalysisMosaicRequestedSubject
	} else if eventType == orchestration.NowcastInputRequestedEventType {
		subject = orchestration.NowcastInputRequestedSubject
	} else if eventType == orchestration.PystepsLKRequestedEventType {
		subject = orchestration.PystepsLKRequestedSubject
	}
	eventID, err := uuid.Parse(event["event_id"].(string))
	if err != nil {
		return fmt.Errorf("parse replay event UUID: %w", err)
	}
	return bus.Publish(ctx, workflow.OutboxEvent{
		ID:      eventID,
		Subject: subject,
		Payload: payload,
	})
}

func environmentOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

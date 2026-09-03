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
	"os"
	"os/signal"
	"path/filepath"
	"slices"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/bdpruntime"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/buildinfo"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/messaging"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operationalmetrics"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/radaringest"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/runtimeconfig"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"gopkg.in/yaml.v3"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	command := "serve"
	if len(os.Args) > 1 {
		command = os.Args[1]
	}
	platformRuntime, err := bdpruntime.Prepare(bdpruntime.ComponentOrchestrator, true)
	if err != nil {
		slog.Error("initialize Ruiyun BDP runtime for RainPulse orchestrator", "error", err)
		os.Exit(1)
	}
	if platformRuntime.PlatformAvailable {
		radarSource, sourceErr := bdpruntime.ResolveOriginalFileSource(
			platformRuntime.Config.RadarInput.DataCode,
			platformRuntime.Config.RadarInput.SourceIndex,
		)
		if sourceErr != nil {
			if platformRuntime.Required() {
				slog.Error("resolve radar input from Ruiyun BDP metadata", "error", sourceErr)
				os.Exit(1)
			}
			slog.Warn("Ruiyun BDP radar metadata unavailable; retaining deployment radar root", "error", sourceErr)
		} else if err := os.Setenv("RAINPULSE_RADAR_INGEST_ROOT", radarSource.Root); err != nil {
			slog.Error("apply Ruiyun BDP radar input root", "error", err)
			os.Exit(1)
		} else {
			slog.Info("Ruiyun BDP radar input resolved", "data_code", radarSource.DataCode,
				"source_index", radarSource.SourceIndex, "root", radarSource.Root)
		}
	}

	pool, store, bus, service, err := dependencies(ctx)
	if err != nil {
		slog.Error("initialize RainPulse orchestrator", "error", err)
		os.Exit(1)
	}
	defer pool.Close()
	defer bus.Close()

	switch command {
	case "serve":
		if err := serve(ctx, store, bus, service); err != nil && !errors.Is(err, context.Canceled) {
			slog.Error("RainPulse orchestrator stopped", "error", err)
			os.Exit(1)
		}
	case "pipeline-once":
		settings, settingsErr := pipelineSettingsFromEnvironment()
		if settingsErr != nil {
			slog.Error("load pipeline settings", "error", settingsErr)
			os.Exit(1)
		}
		if settings == nil {
			slog.Error("pipeline-once requires RAINPULSE_PIPELINE_ENABLED=true")
			os.Exit(2)
		}
		if err := newPipelinePlanner(*settings, store, service).PlanOnce(ctx); err != nil {
			slog.Error("plan RainPulse pipeline once", "error", err)
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
	case "radar-ingest":
		if len(os.Args) != 4 {
			slog.Error("radar-ingest requires config YAML and input path")
			os.Exit(2)
		}
		if err := radarIngest(ctx, service, os.Args[2], os.Args[3], true); err != nil {
			slog.Error("archive and ingest radar volume", "error", err)
			os.Exit(1)
		}
	case "radar-batch-ingest":
		if len(os.Args) != 4 {
			slog.Error("radar-batch-ingest requires a radar config directory and input root")
			os.Exit(2)
		}
		if err := radarBatchIngest(ctx, service, os.Args[2], os.Args[3]); err != nil {
			slog.Error("archive and ingest historical radar volumes", "error", err)
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
	case "forecast-verify":
		if len(os.Args) != 4 {
			slog.Error("forecast-verify requires a forecast run UUID and verification profile YAML")
			os.Exit(2)
		}
		if err := forecastVerification(ctx, store, service, os.Args[2], os.Args[3]); err != nil {
			slog.Error("create forecast verification workflow", "error", err)
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
	RadialInterference    struct {
		Morphology struct {
			ContextFusion qcContextFusionConfiguration `yaml:"context_fusion"`
		} `yaml:"morphology"`
	} `yaml:"radial_interference"`
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
	ExecutionMode     string `yaml:"execution_mode"`
	GridID            string `yaml:"grid_id"`
	GridConfigVersion string `yaml:"grid_config_version"`
	Sequence          struct {
		MinimumFrames   int    `yaml:"minimum_frames"`
		MaximumFrames   int    `yaml:"maximum_frames"`
		TimestepMinutes int    `yaml:"timestep_minutes"`
		Selection       string `yaml:"selection"`
	} `yaml:"sequence"`
	Gates struct {
		MinimumValidCoverageRatio           float64 `yaml:"minimum_valid_coverage_ratio"`
		MinimumMeanQualityIndex             float64 `yaml:"minimum_mean_quality_index"`
		RequireAllFramesOperationalEligible bool    `yaml:"require_all_frames_operational_eligible"`
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

func executionModeOrOperational(value string) string {
	if value == "" {
		return "operational"
	}
	return value
}

type forecastVerificationConfiguration struct {
	SchemaVersion           string    `yaml:"schema_version"`
	ProfileVersion          string    `yaml:"profile_version"`
	Lifecycle               string    `yaml:"lifecycle"`
	ForecastContractVersion string    `yaml:"forecast_contract_version"`
	TruthContractVersion    string    `yaml:"truth_contract_version"`
	ResultContractVersion   string    `yaml:"result_contract_version"`
	LeadMinutes             []int     `yaml:"lead_minutes"`
	Models                  []string  `yaml:"models"`
	ThresholdsMMH           []float64 `yaml:"thresholds_mm_h"`
	FSSWindowsKM            []float64 `yaml:"fss_windows_km"`
	AccumulationWindows     []int     `yaml:"accumulation_windows_minutes"`
	AccumulationThresholds  []float64 `yaml:"accumulation_thresholds_mm"`
	ValidityDomain          string    `yaml:"validity_domain"`
	PromotionEligible       bool      `yaml:"promotion_eligible"`
}

type radarIngestSettings struct {
	configPath string
	root       string
	interval   time.Duration
	minAge     time.Duration
	lookback   time.Duration
}

type radarBatchInput struct {
	radarID    string
	configPath string
	inputPath  string
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
	probedStart, probedEnd, err := radaringest.ProbeVolumeTimes(absoluteInput)
	if err != nil {
		return err
	}
	if !start.Equal(probedStart) || !end.Equal(probedEnd) {
		return fmt.Errorf("provided volume times differ from the authoritative RSTM radial headers")
	}
	stableInfo, err := os.Stat(absoluteInput)
	if err != nil {
		return fmt.Errorf("restat radar input after inspection: %w", err)
	}
	if stableInfo.Size() != info.Size() || !stableInfo.ModTime().Equal(info.ModTime()) {
		return fmt.Errorf("radar input changed while it was being inspected")
	}
	scan, job, err := createRadarDecode(ctx, service, config, configBytes, configJSON, absoluteInput, inputHash, info.Size(), start, end)
	if err != nil {
		return err
	}
	return writeRadarDecodeResult(scan, job)
}

func radarIngest(
	ctx context.Context,
	service *orchestration.Service,
	configPath string,
	inputPath string,
	emitResult bool,
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
	start, end, err := radaringest.ProbeVolumeTimes(absoluteInput)
	if err != nil {
		return err
	}
	inputHash, err := sha256File(absoluteInput)
	if err != nil {
		return err
	}
	stableInfo, err := os.Stat(absoluteInput)
	if err != nil {
		return fmt.Errorf("restat radar input after inspection: %w", err)
	}
	if stableInfo.Size() != info.Size() || !stableInfo.ModTime().Equal(info.ModTime()) {
		return fmt.Errorf("radar input changed while it was being inspected")
	}
	scan, job, err := createRadarDecode(ctx, service, config, configBytes, configJSON, absoluteInput, inputHash, info.Size(), start, end)
	if err != nil || !emitResult {
		return err
	}
	return writeRadarDecodeResult(scan, job)
}

func radarBatchIngest(
	ctx context.Context,
	service *orchestration.Service,
	configDirectory string,
	inputRoot string,
) error {
	inputs, err := discoverRadarBatchInputs(configDirectory, inputRoot)
	if err != nil {
		return err
	}
	counts := make(map[string]int)
	for index, input := range inputs {
		if err := radarIngest(ctx, service, input.configPath, input.inputPath, false); err != nil {
			return fmt.Errorf(
				"ingest historical radar volume %d/%d %s: %w",
				index+1, len(inputs), input.inputPath, err,
			)
		}
		counts[input.radarID]++
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{
		"registered_volume_count": len(inputs),
		"radar_counts":            counts,
	})
}

func discoverRadarBatchInputs(configDirectory string, inputRoot string) ([]radarBatchInput, error) {
	configDirectory, err := filepath.Abs(configDirectory)
	if err != nil {
		return nil, fmt.Errorf("resolve radar config directory: %w", err)
	}
	inputRoot, err = filepath.Abs(inputRoot)
	if err != nil {
		return nil, fmt.Errorf("resolve radar input root: %w", err)
	}
	configEntries, err := os.ReadDir(configDirectory)
	if err != nil {
		return nil, fmt.Errorf("read radar config directory: %w", err)
	}
	configs := make(map[string]string)
	for _, entry := range configEntries {
		if entry.IsDir() || !strings.HasSuffix(strings.ToLower(entry.Name()), ".yaml") {
			continue
		}
		path := filepath.Join(configDirectory, entry.Name())
		content, readErr := os.ReadFile(path)
		if readErr != nil {
			return nil, fmt.Errorf("read radar batch config %s: %w", path, readErr)
		}
		var config radarConfiguration
		if decodeErr := yaml.Unmarshal(content, &config); decodeErr != nil {
			return nil, fmt.Errorf("decode radar batch config %s: %w", path, decodeErr)
		}
		if config.RadarID == "" {
			return nil, fmt.Errorf("radar batch config %s lacks radar_id", path)
		}
		radarID := strings.ToLower(config.RadarID)
		if _, duplicate := configs[radarID]; duplicate {
			return nil, fmt.Errorf("duplicate radar batch config for %s", radarID)
		}
		configs[radarID] = path
	}
	if len(configs) == 0 {
		return nil, fmt.Errorf("radar config directory contains no YAML radar configurations")
	}
	radarIDs := make([]string, 0, len(configs))
	for radarID := range configs {
		radarIDs = append(radarIDs, radarID)
	}
	sort.Strings(radarIDs)
	inputs := make([]radarBatchInput, 0)
	for _, radarID := range radarIDs {
		radarRoot := filepath.Join(inputRoot, strings.ToUpper(radarID))
		if info, statErr := os.Stat(radarRoot); statErr != nil || !info.IsDir() {
			return nil, fmt.Errorf("historical radar input directory is unavailable: %s", radarRoot)
		}
		walkErr := filepath.WalkDir(radarRoot, func(path string, entry os.DirEntry, walkErr error) error {
			if walkErr != nil {
				return walkErr
			}
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), "_CAP_FMT.bin.bz2") {
				return nil
			}
			inputs = append(inputs, radarBatchInput{
				radarID: radarID, configPath: configs[radarID], inputPath: path,
			})
			return nil
		})
		if walkErr != nil {
			return nil, fmt.Errorf("scan historical radar input directory %s: %w", radarRoot, walkErr)
		}
	}
	sort.Slice(inputs, func(i, j int) bool {
		leftKey := radarBatchChronologyKey(inputs[i].inputPath)
		rightKey := radarBatchChronologyKey(inputs[j].inputPath)
		if leftKey == rightKey {
			return inputs[i].radarID < inputs[j].radarID
		}
		return leftKey < rightKey
	})
	if len(inputs) == 0 {
		return nil, fmt.Errorf("historical input root contains no regular CAP_FMT volumes")
	}
	return inputs, nil
}

func radarBatchChronologyKey(path string) string {
	base := filepath.Base(path)
	for _, part := range strings.Split(base, "_") {
		if len(part) != len("20060102150405") {
			continue
		}
		digitsOnly := true
		for _, character := range part {
			if character < '0' || character > '9' {
				digitsOnly = false
				break
			}
		}
		if digitsOnly {
			return part
		}
	}
	return base
}

func createRadarDecode(
	ctx context.Context,
	service *orchestration.Service,
	config radarConfiguration,
	configBytes []byte,
	configJSON json.RawMessage,
	absoluteInput string,
	inputHash string,
	inputSize int64,
	start time.Time,
	end time.Time,
) (workflow.RadarScan, workflow.Job, error) {
	archive, err := radarArchiveFromEnvironment()
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	inputURI, err := archive.File(ctx, config.RadarID, start, inputHash, absoluteInput)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	configHash := sha256.Sum256(configBytes)
	displayName := config.DisplayName
	scan, job, err := service.CreateRadarDecode(ctx, orchestration.RadarDecodeInput{
		RadarID: config.RadarID, DisplayName: &displayName,
		Lifecycle:     workflow.RadarLifecycle(config.Lifecycle),
		ConfigVersion: config.ConfigVersion, Config: configJSON,
		ConfigSHA256: fmt.Sprintf("%x", configHash), SourceFormat: config.Source.Format,
		InputURI:    inputURI,
		InputSHA256: inputHash, InputSizeBytes: inputSize,
		VolumeStartTime: start, VolumeEndTime: end,
	})
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	return scan, job, nil
}

func writeRadarDecodeResult(scan workflow.RadarScan, job workflow.Job) error {
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"scan_id": scan.ID.String(), "run_id": scan.RunID.String(), "job_id": job.ID.String(),
	})
}

func radarArchiveFromEnvironment() (*radaringest.Archive, error) {
	return radaringest.NewArchive(
		environmentOrDefault("RAINPULSE_OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
		os.Getenv("RAINPULSE_MINIO_WORKER_ACCESS_KEY"),
		os.Getenv("RAINPULSE_MINIO_WORKER_SECRET_KEY"),
		environmentOrDefault("RAINPULSE_OBJECT_STORE_BUCKET", "rainpulse"),
	)
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
	scan, job, err := createRadarQC(ctx, store, service, rawScanID, configPath, uuid.Nil)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"scan_id": scan.ID.String(), "run_id": scan.RunID.String(), "job_id": job.ID.String(),
	})
}

func createRadarQC(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawScanID string,
	configPath string,
	regenerationID uuid.UUID,
) (workflow.RadarScan, workflow.Job, error) {
	scanID, err := uuid.Parse(rawScanID)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("parse radar scan UUID: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("read radar QC configuration: %w", err)
	}
	var config qcConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("decode radar QC configuration: %w", err)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("normalize radar QC configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("encode radar QC configuration: %w", err)
	}
	configHash := sha256.Sum256(configBytes)
	scan, err := store.GetRadarScan(ctx, scanID)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	if scan.NormalizedURI == nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("radar scan has no normalized volume")
	}
	health, err := store.GetRadarHealthMetrics(ctx, scanID)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	temporalContext, crossRadarContext, err := selectRadarQCContext(
		ctx,
		store,
		scan,
		config.RadialInterference.Morphology.ContextFusion,
	)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	job, err := service.CreateRadarQC(ctx, orchestration.RadarQCInput{
		ScanID: scan.ID, RunID: scan.RunID, RadarID: scan.RadarID,
		RadarConfigVersion:    scan.RadarConfigVersion,
		NormalizedURI:         *scan.NormalizedURI,
		TemporalContext:       temporalContext,
		CrossRadarContext:     crossRadarContext,
		CurrentStatus:         scan.Status,
		Health:                health.Health,
		QCProfile:             config.ProfileVersion,
		QCPipelineVersion:     config.PipelineVersion,
		FlagDefinitionVersion: config.FlagDefinitionVersion,
		QCConfig:              configJSON,
		QCConfigSHA256:        fmt.Sprintf("%x", configHash),
		RegenerationID:        regenerationID,
	})
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	return scan, job, nil
}

type qcContextFusionConfiguration struct {
	Enabled                         bool `yaml:"enabled"`
	MaximumTemporalContextScans     int  `yaml:"maximum_temporal_context_scans"`
	CrossRadarMaximumTimeOffsetSecs int  `yaml:"cross_radar_max_time_offset_seconds"`
}

func selectRadarQCContext(
	ctx context.Context,
	store *postgresstore.Store,
	scan workflow.RadarScan,
	config qcContextFusionConfiguration,
) ([]orchestration.RadarQCContextInput, []orchestration.RadarQCContextInput, error) {
	if !config.Enabled {
		return nil, nil, nil
	}
	// ListRadarScans is deliberately bounded by the store contract (200).  This
	// is enough to select the nearest three temporal scans and one scan per
	// neighbouring radar without making regeneration fail before QC is queued.
	candidates, err := store.ListRadarScans(ctx, 200, nil, nil)
	if err != nil {
		return nil, nil, fmt.Errorf("list radar scans for QC context: %w", err)
	}
	temporal, crossRadar := radarQCContextFromScans(scan, candidates, config)
	return temporal, crossRadar, nil
}

func radarQCContextFromScans(
	scan workflow.RadarScan,
	candidates []workflow.RadarScan,
	config qcContextFusionConfiguration,
) ([]orchestration.RadarQCContextInput, []orchestration.RadarQCContextInput) {
	if !config.Enabled {
		return nil, nil
	}
	maximumTemporal := config.MaximumTemporalContextScans
	if maximumTemporal <= 0 || maximumTemporal > 3 {
		maximumTemporal = 3
	}
	maximumCrossOffset := time.Duration(config.CrossRadarMaximumTimeOffsetSecs) * time.Second
	if maximumCrossOffset <= 0 {
		maximumCrossOffset = 5 * time.Minute
	}
	temporalCandidates := make([]workflow.RadarScan, 0, maximumTemporal)
	crossByRadar := make(map[string]workflow.RadarScan)
	for _, candidate := range candidates {
		if candidate.ID == scan.ID || candidate.NormalizedURI == nil || *candidate.NormalizedURI == "" {
			continue
		}
		if candidate.RadarID == scan.RadarID {
			temporalCandidates = append(temporalCandidates, candidate)
			continue
		}
		offset := absoluteDuration(candidate.VolumeEndTime.Sub(scan.VolumeEndTime))
		if offset > maximumCrossOffset {
			continue
		}
		current, exists := crossByRadar[candidate.RadarID]
		if !exists || offset < absoluteDuration(current.VolumeEndTime.Sub(scan.VolumeEndTime)) ||
			(offset == absoluteDuration(current.VolumeEndTime.Sub(scan.VolumeEndTime)) && candidate.ID.String() < current.ID.String()) {
			crossByRadar[candidate.RadarID] = candidate
		}
	}
	sort.Slice(temporalCandidates, func(left, right int) bool {
		leftOffset := absoluteDuration(temporalCandidates[left].VolumeEndTime.Sub(scan.VolumeEndTime))
		rightOffset := absoluteDuration(temporalCandidates[right].VolumeEndTime.Sub(scan.VolumeEndTime))
		if leftOffset == rightOffset {
			return temporalCandidates[left].ID.String() < temporalCandidates[right].ID.String()
		}
		return leftOffset < rightOffset
	})
	if len(temporalCandidates) > maximumTemporal {
		temporalCandidates = temporalCandidates[:maximumTemporal]
	}
	temporal := make([]orchestration.RadarQCContextInput, 0, len(temporalCandidates))
	for _, candidate := range temporalCandidates {
		temporal = append(temporal, orchestration.RadarQCContextInput{
			RadarID: candidate.RadarID, InputURI: *candidate.NormalizedURI,
		})
	}
	radarIDs := make([]string, 0, len(crossByRadar))
	for radarID := range crossByRadar {
		radarIDs = append(radarIDs, radarID)
	}
	sort.Strings(radarIDs)
	if len(radarIDs) > 3 {
		radarIDs = radarIDs[:3]
	}
	crossRadar := make([]orchestration.RadarQCContextInput, 0, len(radarIDs))
	for _, radarID := range radarIDs {
		candidate := crossByRadar[radarID]
		crossRadar = append(crossRadar, orchestration.RadarQCContextInput{
			RadarID: candidate.RadarID, InputURI: *candidate.NormalizedURI,
		})
	}
	return temporal, crossRadar
}

func radarGrid(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawScanID string,
	configPath string,
) error {
	scan, job, err := createRadarGrid(ctx, store, service, rawScanID, configPath, uuid.Nil)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"scan_id": scan.ID.String(), "run_id": scan.RunID.String(), "job_id": job.ID.String(),
	})
}

func createRadarGrid(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawScanID string,
	configPath string,
	regenerationID uuid.UUID,
) (workflow.RadarScan, workflow.Job, error) {
	scanID, err := uuid.Parse(rawScanID)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("parse radar scan UUID: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("read radar grid configuration: %w", err)
	}
	var config gridConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("decode radar grid configuration: %w", err)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("normalize radar grid configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("encode radar grid configuration: %w", err)
	}
	configHash := sha256.Sum256(configBytes)
	scan, err := store.GetRadarScan(ctx, scanID)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	if scan.QCURI == nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("radar scan has no QC volume")
	}
	job, err := service.CreateRadarGrid(ctx, orchestration.RadarGridInput{
		ScanID: scan.ID, RunID: scan.RunID, RadarID: scan.RadarID,
		QCURI: *scan.QCURI, CurrentStatus: scan.Status,
		GridID: config.GridID, GridConfigVersion: config.GridConfigVersion,
		GridProfileVersion: config.ProfileVersion,
		HybridScanVersion:  config.AlgorithmVersion,
		GridConfig:         configJSON, GridConfigSHA256: fmt.Sprintf("%x", configHash),
		RegenerationID: regenerationID,
	})
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	return scan, job, nil
}

func analysisMosaic(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawAnalysisTime string,
	configPath string,
	rawScanIDs []string,
) error {
	analysis, job, err := createAnalysisMosaic(
		ctx, store, service, rawAnalysisTime, configPath, rawScanIDs, uuid.Nil,
	)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"analysis_id": analysis.ID.String(),
		"run_id":      analysis.RunID.String(),
		"job_id":      job.ID.String(),
	})
}

func createAnalysisMosaic(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawAnalysisTime string,
	configPath string,
	rawScanIDs []string,
	regenerationID uuid.UUID,
) (workflow.AnalysisCycle, workflow.Job, error) {
	analysisTime, err := time.Parse(time.RFC3339Nano, rawAnalysisTime)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("parse analysis time: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("read radar mosaic configuration: %w", err)
	}
	var config mosaicConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("decode radar mosaic configuration: %w", err)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("normalize radar mosaic configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("encode radar mosaic configuration: %w", err)
	}
	candidates := make([]orchestration.AnalysisMosaicCandidate, 0, len(rawScanIDs))
	for _, rawScanID := range rawScanIDs {
		scanID, err := uuid.Parse(rawScanID)
		if err != nil {
			return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("parse radar scan UUID %q: %w", rawScanID, err)
		}
		scan, err := store.GetRadarScan(ctx, scanID)
		if err != nil {
			return workflow.AnalysisCycle{}, workflow.Job{}, err
		}
		if scan.GridURI == nil {
			return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("radar scan %s has no committed RadarGrid", scan.ID)
		}
		gridMetrics, err := store.GetRadarGridMetrics(ctx, scanID)
		if err != nil {
			return workflow.AnalysisCycle{}, workflow.Job{}, err
		}
		if gridMetrics.GridID != config.GridID ||
			gridMetrics.GridConfigVersion != config.GridConfigVersion {
			return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("radar scan %s uses a different target grid", scan.ID)
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
		RegenerationID:     regenerationID,
	})
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, err
	}
	return analysis, job, nil
}

func analysisQPE(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawAnalysisID string,
	configPath string,
) error {
	analysis, job, err := createAnalysisQPE(
		ctx, store, service, rawAnalysisID, configPath, uuid.Nil,
	)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"analysis_id": analysis.ID.String(),
		"run_id":      analysis.RunID.String(),
		"job_id":      job.ID.String(),
	})
}

func createAnalysisQPE(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawAnalysisID string,
	configPath string,
	regenerationID uuid.UUID,
) (workflow.AnalysisCycle, workflow.Job, error) {
	analysisID, err := uuid.Parse(rawAnalysisID)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("parse analysis UUID: %w", err)
	}
	analysis, err := store.GetAnalysisCycle(ctx, analysisID)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, err
	}
	if analysis.MosaicURI == nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("analysis %s has no committed RadarMosaic", analysis.ID)
	}
	mosaicMetrics, err := store.GetAnalysisMosaicMetrics(ctx, analysisID)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, err
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("read QPE configuration: %w", err)
	}
	var config qpeConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("decode QPE configuration: %w", err)
	}
	if config.GridID != analysis.GridID ||
		config.GridConfigVersion != mosaicMetrics.GridConfigVersion {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("QPE configuration uses a different target grid")
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("normalize QPE configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("encode QPE configuration: %w", err)
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
		RegenerationID: regenerationID,
	})
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, err
	}
	return analysis, job, nil
}

func analysisDiagnostics(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawAnalysisID string,
	configPath string,
) error {
	analysis, job, err := createAnalysisDiagnostics(
		ctx, store, service, rawAnalysisID, configPath, uuid.Nil,
	)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{
		"analysis_id": analysis.ID.String(),
		"run_id":      analysis.RunID.String(),
		"job_id":      job.ID.String(),
	})
}

func createAnalysisDiagnostics(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawAnalysisID string,
	configPath string,
	regenerationID uuid.UUID,
) (workflow.AnalysisCycle, workflow.Job, error) {
	analysisID, err := uuid.Parse(rawAnalysisID)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("parse analysis UUID: %w", err)
	}
	analysis, err := store.GetAnalysisCycle(ctx, analysisID)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, err
	}
	if analysis.AnalysisURI == nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("analysis %s has no committed RadarAnalysis", analysis.ID)
	}
	inputs := make([]workflow.AnalysisDiagnosticRadarInput, 0, len(analysis.Radars))
	for _, radar := range analysis.Radars {
		if radar.State != workflow.AnalysisRadarParticipating || radar.ScanID == nil {
			continue
		}
		scan, scanErr := store.GetRadarScan(ctx, *radar.ScanID)
		if scanErr != nil {
			return workflow.AnalysisCycle{}, workflow.Job{}, scanErr
		}
		if scan.RadarID != radar.RadarID || scan.QCURI == nil {
			return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("analysis contributor %s has no exact QCRadarVolume", radar.RadarID)
		}
		inputs = append(inputs, workflow.AnalysisDiagnosticRadarInput{
			RadarID: radar.RadarID,
			ScanID:  *radar.ScanID,
			QCURI:   *scan.QCURI,
		})
	}
	if len(inputs) == 0 {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("analysis %s has no participating QC radar inputs", analysis.ID)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("read diagnostic configuration: %w", err)
	}
	var config diagnosticConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("decode diagnostic configuration: %w", err)
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("normalize diagnostic configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf("encode diagnostic configuration: %w", err)
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
		RegenerationID:          regenerationID,
	})
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, err
	}
	return analysis, job, nil
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
		GridConfigVersion:                   config.GridConfigVersion,
		PreprocessVersion:                   config.BuilderVersion,
		GateConfigVersion:                   config.ProfileVersion,
		ExecutionMode:                       executionModeOrOperational(config.ExecutionMode),
		RequireAllFramesOperationalEligible: config.Gates.RequireAllFramesOperationalEligible,
		MinimumFrames:                       config.Sequence.MinimumFrames,
		MaximumFrames:                       config.Sequence.MaximumFrames,
		Timestep:                            time.Duration(config.Sequence.TimestepMinutes) * time.Minute,
		MinimumValidCoverageRatio:           config.Gates.MinimumValidCoverageRatio,
		MinimumMeanQualityIndex:             config.Gates.MinimumMeanQualityIndex,
		Candidates:                          candidates, Config: configJSON,
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

func createRegeneratedNowcastInput(
	ctx context.Context,
	service *orchestration.Service,
	request workflow.PipelineRegeneration,
	analyses []workflow.AnalysisCycle,
	configPath string,
) (workflow.Run, workflow.Job, error) {
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("read regenerated NowcastInput configuration: %w", err)
	}
	var config nowcastInputConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("decode regenerated NowcastInput configuration: %w", err)
	}
	if config.Sequence.Selection != "latest_contiguous" || config.GridID != request.GridID {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("regenerated NowcastInput configuration differs from request lineage")
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("normalize regenerated NowcastInput configuration: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("encode regenerated NowcastInput configuration: %w", err)
	}
	candidates := make([]orchestration.NowcastInputCandidate, 0, len(analyses))
	for _, analysis := range analyses {
		if analysis.Status != workflow.AnalysisReady || analysis.AnalysisURI == nil ||
			analysis.ValidCoverageRatio == nil || analysis.MeanQualityIndex == nil {
			return workflow.Run{}, workflow.Job{}, fmt.Errorf("regenerated analysis %s is not ready for NowcastInput", analysis.ID)
		}
		candidates = append(candidates, orchestration.NowcastInputCandidate{
			AnalysisID: analysis.ID, AnalysisTime: analysis.AnalysisTime,
			GridID: analysis.GridID, AnalysisURI: *analysis.AnalysisURI,
			CurrentStatus: analysis.Status, OperationalEligible: analysis.DegradedReason == nil,
			ValidCoverageRatio: *analysis.ValidCoverageRatio,
			MeanQualityIndex:   *analysis.MeanQualityIndex,
		})
	}
	configHash := sha256.Sum256(configBytes)
	return service.CreateNowcastInput(ctx, orchestration.NowcastInputInput{
		IssueTime: request.IssueTime, GridID: config.GridID,
		GridConfigVersion:                   config.GridConfigVersion,
		PreprocessVersion:                   config.BuilderVersion,
		GateConfigVersion:                   config.ProfileVersion,
		ExecutionMode:                       executionModeOrOperational(config.ExecutionMode),
		RequireAllFramesOperationalEligible: config.Gates.RequireAllFramesOperationalEligible,
		MinimumFrames:                       config.Sequence.MinimumFrames,
		MaximumFrames:                       config.Sequence.MaximumFrames,
		Timestep:                            time.Duration(config.Sequence.TimestepMinutes) * time.Minute,
		MinimumValidCoverageRatio:           config.Gates.MinimumValidCoverageRatio,
		MinimumMeanQualityIndex:             config.Gates.MinimumMeanQualityIndex,
		Candidates:                          candidates,
		Config:                              configJSON,
		ConfigSHA256:                        fmt.Sprintf("%x", configHash),
		RerunOf:                             &request.SourceRun,
		RegenerationID:                      request.RequestID,
		Reason:                              request.Reason,
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

func forecastVerification(
	ctx context.Context,
	store *postgresstore.Store,
	service *orchestration.Service,
	rawRunID string,
	configPath string,
) error {
	runID, err := uuid.Parse(rawRunID)
	if err != nil {
		return fmt.Errorf("parse forecast-verification run UUID: %w", err)
	}
	configBytes, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("read forecast-verification profile: %w", err)
	}
	var config forecastVerificationConfiguration
	if err := yaml.Unmarshal(configBytes, &config); err != nil {
		return fmt.Errorf("decode forecast-verification profile: %w", err)
	}
	if err := validateForecastVerificationConfiguration(config); err != nil {
		return err
	}
	var configValue map[string]any
	if err := yaml.Unmarshal(configBytes, &configValue); err != nil {
		return fmt.Errorf("normalize forecast-verification profile: %w", err)
	}
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		return fmt.Errorf("encode forecast-verification profile: %w", err)
	}
	input, err := store.GetForecastVerificationInput(ctx, runID)
	if err != nil {
		return err
	}
	configHash := sha256.Sum256(configBytes)
	input.VerificationConfigVersion = config.ProfileVersion
	input.ForecastContractVersion = config.ForecastContractVersion
	input.ResultContractVersion = config.ResultContractVersion
	input.VerificationConfig = configJSON
	input.VerificationConfigSHA256 = fmt.Sprintf("%x", configHash)
	run, job, err := service.CreateForecastVerification(ctx, input)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{
		"run_id": run.ID.String(), "job_id": job.ID.String(),
		"issue_time": run.IssueTime, "truth_frame_count": len(input.Truth),
	})
}

func validateForecastVerificationConfiguration(config forecastVerificationConfiguration) error {
	expectedLeads := make([]int, 24)
	for index := range expectedLeads {
		expectedLeads[index] = (index + 1) * 5
	}
	if config.SchemaVersion != "1.0" || config.ProfileVersion == "" ||
		config.Lifecycle != "automatic_verification" ||
		config.ForecastContractVersion != "1.1" || config.TruthContractVersion != "1.2" ||
		config.ResultContractVersion != "1.0" ||
		!slices.Equal(config.LeadMinutes, expectedLeads) ||
		!slices.Equal(config.Models, []string{"lk", "persistence", "translation"}) ||
		len(config.ThresholdsMMH) == 0 || len(config.FSSWindowsKM) == 0 ||
		!slices.Equal(config.AccumulationWindows, []int{60, 120}) ||
		len(config.AccumulationThresholds) == 0 || config.ValidityDomain != "common" ||
		config.PromotionEligible {
		return fmt.Errorf("forecast-verification profile differs from RP-031")
	}
	return nil
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
	ingestSettings, err := radarIngestSettingsFromEnvironment()
	if err != nil {
		return err
	}
	if ingestSettings != nil {
		scanner, err := radaringest.NewScanner(
			ingestSettings.root,
			ingestSettings.minAge,
			ingestSettings.lookback,
		)
		if err != nil {
			return err
		}
		go radarIngestLoop(ctx, scanner, *ingestSettings, service)
	}
	pipelineSettings, err := pipelineSettingsFromEnvironment()
	if err != nil {
		return err
	}
	if pipelineSettings != nil {
		go newPipelinePlanner(*pipelineSettings, store, service).Run(ctx)
	}
	metricsHandler := operationalmetrics.Handler(store, buildinfo.Identity())
	healthServer := &http.Server{
		Addr:              environmentOrDefault("RAINPULSE_ORCHESTRATOR_HEALTH_ADDR", ":8090"),
		ReadHeaderTimeout: 3 * time.Second,
		Handler: http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
			if request.URL.Path == "/metrics" {
				metricsHandler.ServeHTTP(response, request)
				return
			}
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

	slog.Info("RainPulse orchestrator ready", "version", buildinfo.Identity())
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

func radarIngestSettingsFromEnvironment() (*radarIngestSettings, error) {
	rawEnabled := environmentOrDefault("RAINPULSE_RADAR_INGEST_ENABLED", "false")
	enabled, err := strconv.ParseBool(rawEnabled)
	if err != nil {
		return nil, fmt.Errorf("parse RAINPULSE_RADAR_INGEST_ENABLED: %w", err)
	}
	if !enabled {
		return nil, nil
	}
	settings := radarIngestSettings{
		configPath: os.Getenv("RAINPULSE_RADAR_INGEST_CONFIG"),
		root:       os.Getenv("RAINPULSE_RADAR_INGEST_ROOT"),
	}
	if settings.configPath == "" || settings.root == "" {
		return nil, fmt.Errorf("enabled radar ingest requires config and arrival root")
	}
	if info, statErr := os.Stat(settings.configPath); statErr != nil || !info.Mode().IsRegular() {
		return nil, fmt.Errorf("radar ingest config must be a readable regular file")
	}
	for name, item := range map[string]struct {
		fallback string
		target   *time.Duration
	}{
		"RAINPULSE_RADAR_INGEST_INTERVAL": {"15s", &settings.interval},
		"RAINPULSE_RADAR_INGEST_MIN_AGE":  {"30s", &settings.minAge},
		"RAINPULSE_RADAR_INGEST_LOOKBACK": {"24h", &settings.lookback},
	} {
		value, parseErr := time.ParseDuration(environmentOrDefault(name, item.fallback))
		if parseErr != nil || value < 0 {
			return nil, fmt.Errorf("parse %s as a non-negative duration", name)
		}
		*item.target = value
	}
	if settings.interval <= 0 {
		return nil, fmt.Errorf("RAINPULSE_RADAR_INGEST_INTERVAL must be positive")
	}
	return &settings, nil
}

func radarIngestLoop(
	ctx context.Context,
	scanner *radaringest.Scanner,
	settings radarIngestSettings,
	service *orchestration.Service,
) {
	ticker := time.NewTicker(settings.interval)
	defer ticker.Stop()
	for {
		paths, err := scanner.Scan(time.Now().UTC())
		if err != nil {
			slog.Error("scan radar arrival directory", "error", err)
		} else {
			for _, path := range paths {
				if err := radarIngest(ctx, service, settings.configPath, path, false); err != nil {
					slog.Error("ingest radar arrival file", "path", path, "error", err)
					continue
				}
				scanner.MarkProcessed(path)
				slog.Info("radar arrival archived and registered", "path", path)
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
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
	subject, err := requestedSubject(eventType)
	if err != nil {
		return err
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

func requestedSubject(eventType string) (string, error) {
	switch eventType {
	case orchestration.JobRequestedEventType:
		return orchestration.JobRequestedSubject, nil
	case orchestration.RadarDecodeRequestedEventType:
		return orchestration.RadarDecodeRequestedSubject, nil
	case orchestration.RadarQCRequestedEventType:
		return orchestration.RadarQCRequestedSubject, nil
	case orchestration.RadarGridRequestedEventType:
		return orchestration.RadarGridRequestedSubject, nil
	case orchestration.AnalysisMosaicRequestedEventType:
		return orchestration.AnalysisMosaicRequestedSubject, nil
	case orchestration.AnalysisQPERequestedEventType:
		return orchestration.AnalysisQPERequestedSubject, nil
	case orchestration.AnalysisDiagnosticsRequestedEventType:
		return orchestration.AnalysisDiagnosticsRequestedSubject, nil
	case orchestration.NowcastInputRequestedEventType:
		return orchestration.NowcastInputRequestedSubject, nil
	case orchestration.PystepsLKRequestedEventType:
		return orchestration.PystepsLKRequestedSubject, nil
	case orchestration.ProductBuildRequestedEventType:
		return orchestration.ProductBuildRequestedSubject, nil
	default:
		return "", fmt.Errorf("unsupported replay event type %q", eventType)
	}
}

func environmentOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

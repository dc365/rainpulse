package orchestration

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"regexp"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

const (
	SchemaVersion                          = "1.0"
	JobRequestedEventType                  = "job.requested"
	JobCompletedEventType                  = "job.completed"
	JobFailedEventType                     = "job.failed"
	JobRequestedSubject                    = "rainpulse.jobs.requested.model_pysteps_lk"
	RadarDecodeRequestedEventType          = "radar.decode.requested.v1"
	RadarDecodeRequestedSubject            = "rainpulse.jobs.requested.radar_decode"
	RadarDecodeJobType                     = "radar.decode"
	RadarDecoderVersion                    = "cma-rstm-2.1.0"
	RadarQCRequestedEventType              = "radar.qc.requested.v1"
	RadarQCRequestedSubject                = "rainpulse.jobs.requested.radar_qc"
	RadarQCJobType                         = "radar.qc"
	RadarGridRequestedEventType            = "radar.grid.requested.v1"
	RadarGridRequestedSubject              = "rainpulse.jobs.requested.radar_grid"
	RadarGridJobType                       = "radar.grid"
	AnalysisMosaicRequestedEventType       = "analysis.mosaic.requested.v2"
	AnalysisMosaicRequestedSubject         = "rainpulse.jobs.requested.analysis_mosaic"
	AnalysisMosaicJobType                  = "analysis.mosaic"
	AnalysisQPERequestedEventType          = "analysis.qpe.requested.v1"
	AnalysisQPERequestedSubject            = "rainpulse.jobs.requested.analysis_qpe"
	AnalysisQPEJobType                     = "analysis.qpe"
	AnalysisDiagnosticsRequestedEventType  = "analysis.diagnostics.requested.v1"
	AnalysisDiagnosticsRequestedSubject    = "rainpulse.jobs.requested.analysis_diagnostics"
	AnalysisDiagnosticsJobType             = "analysis.diagnostics"
	NowcastInputRequestedEventType         = "nowcast.input.requested.v1"
	NowcastInputRequestedSubject           = "rainpulse.jobs.requested.nowcast_input"
	NowcastInputJobType                    = "nowcast.input"
	NowcastInputReadyEventType             = "nowcast.input.ready.v1"
	NowcastInputReadySubject               = "rainpulse.jobs.lifecycle.nowcast_input_ready"
	PystepsLKRequestedEventType            = "forecast.pysteps_lk.requested.v1"
	PystepsLKRequestedSubject              = "rainpulse.jobs.requested.pysteps_lk"
	PystepsLKJobType                       = "model.pysteps_lk"
	PystepsLKModelID                       = "pysteps-lk"
	PystepsLKModelVersion                  = "pysteps-lk-1.1.0"
	PystepsLKConfidenceKind                = "technical_forecast_quality_index_not_calibrated_probability"
	ForecastBaselineReadyEventType         = "forecast.baseline.ready.v1"
	ForecastBaselineReadySubject           = "rainpulse.jobs.lifecycle.forecast_baseline_ready"
	ProductBuildRequestedEventType         = "product.build.requested.v1"
	ProductBuildRequestedSubject           = "rainpulse.jobs.requested.product_build"
	ProductBuildJobType                    = "product.build"
	ProductPublishedEventType              = "product.published"
	ProductPublishedSubject                = "rainpulse.products.published"
	ForecastVerificationRequestedEventType = "forecast.verification.requested.v1"
	ForecastVerificationRequestedSubject   = "rainpulse.jobs.requested.forecast_verification"
	ForecastVerificationJobType            = "verification.run"
	JobCompletedSubject                    = "rainpulse.jobs.completed"
	JobFailedSubject                       = "rainpulse.jobs.failed"
	JobResultsSubject                      = "rainpulse.jobs.*"
	JobStreamName                          = "RAINPULSE_JOBS"
	ResultConsumerName                     = "rainpulse-orchestrator-results-v2"
	SimulationJobType                      = "model.pysteps_lk"
	SimulationModelID                      = "pysteps-lk-sim"
	SimulationModelVersion                 = "pysteps-lk-sim-v1"
	SimulationConfig                       = "rp003-sim-v1"
	SimulationGrid                         = "rp003-sim-grid"
)

type JobRequested struct {
	SchemaVersion string              `json:"schema_version"`
	EventID       uuid.UUID           `json:"event_id"`
	EventType     string              `json:"event_type"`
	OccurredAt    time.Time           `json:"occurred_at"`
	RunID         uuid.UUID           `json:"run_id"`
	JobID         uuid.UUID           `json:"job_id"`
	TraceID       uuid.UUID           `json:"trace_id"`
	Payload       JobRequestedPayload `json:"payload"`
}

type JobRequestedPayload struct {
	JobType      string         `json:"job_type"`
	InputURI     string         `json:"input_uri"`
	OutputPrefix string         `json:"output_prefix"`
	GridID       string         `json:"grid_id"`
	Config       string         `json:"config_version"`
	Model        string         `json:"model_version"`
	IssueTime    time.Time      `json:"issue_time"`
	InputAssets  []uuid.UUID    `json:"input_asset_ids,omitempty"`
	Parameters   map[string]any `json:"parameters,omitempty"`
}

type RadarDecodeRequested struct {
	SchemaVersion string                      `json:"schema_version"`
	EventID       uuid.UUID                   `json:"event_id"`
	EventType     string                      `json:"event_type"`
	OccurredAt    time.Time                   `json:"occurred_at"`
	RunID         uuid.UUID                   `json:"run_id"`
	JobID         uuid.UUID                   `json:"job_id"`
	TraceID       uuid.UUID                   `json:"trace_id"`
	Payload       RadarDecodeRequestedPayload `json:"payload"`
}

type RadarDecodeRequestedPayload struct {
	ScanID         uuid.UUID `json:"scan_id"`
	AssetID        uuid.UUID `json:"asset_id"`
	RadarID        string    `json:"radar_id"`
	InputURI       string    `json:"input_uri"`
	InputSHA256    string    `json:"input_sha256"`
	InputSizeBytes int64     `json:"input_size_bytes"`
	OutputPrefix   string    `json:"output_prefix"`
	SourceFormat   string    `json:"source_format"`
	RadarConfig    string    `json:"radar_config_version"`
	DecoderVersion string    `json:"decoder_version"`
}

type RadarQCRequested struct {
	SchemaVersion string                  `json:"schema_version"`
	EventID       uuid.UUID               `json:"event_id"`
	EventType     string                  `json:"event_type"`
	OccurredAt    time.Time               `json:"occurred_at"`
	RunID         uuid.UUID               `json:"run_id"`
	JobID         uuid.UUID               `json:"job_id"`
	TraceID       uuid.UUID               `json:"trace_id"`
	Payload       RadarQCRequestedPayload `json:"payload"`
}

type RadarQCRequestedPayload struct {
	ScanID                uuid.UUID `json:"scan_id"`
	RadarID               string    `json:"radar_id"`
	InputURI              string    `json:"input_uri"`
	OutputPrefix          string    `json:"output_prefix"`
	RadarConfig           string    `json:"radar_config_version"`
	QCProfile             string    `json:"qc_profile"`
	QCPipelineVersion     string    `json:"qc_pipeline_version"`
	FlagDefinitionVersion string    `json:"flag_definition_version"`
}

type RadarGridRequested struct {
	SchemaVersion string                    `json:"schema_version"`
	EventID       uuid.UUID                 `json:"event_id"`
	EventType     string                    `json:"event_type"`
	OccurredAt    time.Time                 `json:"occurred_at"`
	RunID         uuid.UUID                 `json:"run_id"`
	JobID         uuid.UUID                 `json:"job_id"`
	TraceID       uuid.UUID                 `json:"trace_id"`
	Payload       RadarGridRequestedPayload `json:"payload"`
}

type RadarGridRequestedPayload struct {
	ScanID            uuid.UUID `json:"scan_id"`
	RadarID           string    `json:"radar_id"`
	InputURI          string    `json:"input_uri"`
	OutputPrefix      string    `json:"output_prefix"`
	GridID            string    `json:"grid_id"`
	GridConfig        string    `json:"grid_config_version"`
	HybridScanVersion string    `json:"hybrid_scan_version"`
}

type AnalysisMosaicRequested struct {
	SchemaVersion string                         `json:"schema_version"`
	EventID       uuid.UUID                      `json:"event_id"`
	EventType     string                         `json:"event_type"`
	OccurredAt    time.Time                      `json:"occurred_at"`
	RunID         uuid.UUID                      `json:"run_id"`
	JobID         uuid.UUID                      `json:"job_id"`
	TraceID       uuid.UUID                      `json:"trace_id"`
	Payload       AnalysisMosaicRequestedPayload `json:"payload"`
}

type AnalysisMosaicRequestedInput struct {
	RadarID           string    `json:"radar_id"`
	ScanID            uuid.UUID `json:"scan_id"`
	GridURI           string    `json:"grid_uri"`
	TimeOffsetSeconds int       `json:"time_offset_seconds"`
	HybridScanVersion string    `json:"hybrid_scan_version"`
}

type AnalysisMosaicRequestedPayload struct {
	AnalysisID          uuid.UUID                      `json:"analysis_id"`
	AnalysisTime        time.Time                      `json:"analysis_time"`
	GridID              string                         `json:"grid_id"`
	GridConfigVersion   string                         `json:"grid_config_version"`
	Inputs              []AnalysisMosaicRequestedInput `json:"inputs"`
	OutputPrefix        string                         `json:"output_prefix"`
	MosaicConfigVersion string                         `json:"mosaic_config_version"`
	MosaicAlgorithm     string                         `json:"mosaic_algorithm_version"`
	FlagDefinition      string                         `json:"flag_definition_version"`
}

type AnalysisQPERequested struct {
	SchemaVersion string                      `json:"schema_version"`
	EventID       uuid.UUID                   `json:"event_id"`
	EventType     string                      `json:"event_type"`
	OccurredAt    time.Time                   `json:"occurred_at"`
	RunID         uuid.UUID                   `json:"run_id"`
	JobID         uuid.UUID                   `json:"job_id"`
	TraceID       uuid.UUID                   `json:"trace_id"`
	Payload       AnalysisQPERequestedPayload `json:"payload"`
}

type AnalysisQPERequestedPayload struct {
	AnalysisID            uuid.UUID `json:"analysis_id"`
	AnalysisTime          time.Time `json:"analysis_time"`
	GridID                string    `json:"grid_id"`
	GridConfigVersion     string    `json:"grid_config_version"`
	InputURI              string    `json:"input_uri"`
	OutputPrefix          string    `json:"output_prefix"`
	MosaicConfigVersion   string    `json:"mosaic_config_version"`
	MosaicAlgorithm       string    `json:"mosaic_algorithm_version"`
	QPEConfigVersion      string    `json:"qpe_config_version"`
	QPEAlgorithmVersion   string    `json:"qpe_algorithm_version"`
	FlagDefinitionVersion string    `json:"flag_definition_version"`
}

type AnalysisDiagnosticsRequested struct {
	SchemaVersion string                              `json:"schema_version"`
	EventID       uuid.UUID                           `json:"event_id"`
	EventType     string                              `json:"event_type"`
	OccurredAt    time.Time                           `json:"occurred_at"`
	RunID         uuid.UUID                           `json:"run_id"`
	JobID         uuid.UUID                           `json:"job_id"`
	TraceID       uuid.UUID                           `json:"trace_id"`
	Payload       AnalysisDiagnosticsRequestedPayload `json:"payload"`
}

type AnalysisDiagnosticsRequestedPayload struct {
	AnalysisID            uuid.UUID                               `json:"analysis_id"`
	AnalysisTime          time.Time                               `json:"analysis_time"`
	GridID                string                                  `json:"grid_id"`
	InputURI              string                                  `json:"input_uri"`
	RadarInputs           []workflow.AnalysisDiagnosticRadarInput `json:"radar_inputs"`
	OutputPrefix          string                                  `json:"output_prefix"`
	DiagnosticConfig      string                                  `json:"diagnostic_config_version"`
	RendererVersion       string                                  `json:"renderer_version"`
	FlagDefinitionVersion string                                  `json:"flag_definition_version"`
}

type NowcastInputRequested struct {
	SchemaVersion string                       `json:"schema_version"`
	EventID       uuid.UUID                    `json:"event_id"`
	EventType     string                       `json:"event_type"`
	OccurredAt    time.Time                    `json:"occurred_at"`
	RunID         uuid.UUID                    `json:"run_id"`
	JobID         uuid.UUID                    `json:"job_id"`
	TraceID       uuid.UUID                    `json:"trace_id"`
	Payload       NowcastInputRequestedPayload `json:"payload"`
}

type NowcastInputRequestedPayload struct {
	AnalysisIDs       []uuid.UUID `json:"analysis_ids"`
	InputURIs         []string    `json:"input_uris"`
	OutputPrefix      string      `json:"output_prefix"`
	IssueTime         time.Time   `json:"issue_time"`
	GridID            string      `json:"grid_id"`
	PreprocessVersion string      `json:"preprocess_version"`
	GateConfigVersion string      `json:"gate_config_version"`
	ExecutionMode     string      `json:"execution_mode"`
}

type NowcastInputReady struct {
	SchemaVersion string                   `json:"schema_version"`
	EventID       uuid.UUID                `json:"event_id"`
	EventType     string                   `json:"event_type"`
	OccurredAt    time.Time                `json:"occurred_at"`
	RunID         uuid.UUID                `json:"run_id"`
	JobID         uuid.UUID                `json:"job_id"`
	TraceID       uuid.UUID                `json:"trace_id"`
	Payload       NowcastInputReadyPayload `json:"payload"`
}

type NowcastInputReadyPayload struct {
	InputURI           string      `json:"input_uri"`
	IssueTime          time.Time   `json:"issue_time"`
	GridID             string      `json:"grid_id"`
	AnalysisIDs        []uuid.UUID `json:"analysis_ids"`
	FrameCount         int         `json:"frame_count"`
	TimestepMinutes    int         `json:"timestep_minutes"`
	ValidCoverageRatio float64     `json:"valid_coverage_ratio"`
	MeanQualityIndex   float64     `json:"mean_quality_index"`
	MaxDataAgeMinutes  float64     `json:"max_data_age_minutes"`
	PreprocessVersion  string      `json:"preprocess_version"`
}

type PystepsLKRequested struct {
	SchemaVersion string                    `json:"schema_version"`
	EventID       uuid.UUID                 `json:"event_id"`
	EventType     string                    `json:"event_type"`
	OccurredAt    time.Time                 `json:"occurred_at"`
	RunID         uuid.UUID                 `json:"run_id"`
	JobID         uuid.UUID                 `json:"job_id"`
	TraceID       uuid.UUID                 `json:"trace_id"`
	Payload       PystepsLKRequestedPayload `json:"payload"`
}

type PystepsLKRequestedPayload struct {
	InputURI                string      `json:"input_uri"`
	OutputPrefix            string      `json:"output_prefix"`
	IssueTime               time.Time   `json:"issue_time"`
	GridID                  string      `json:"grid_id"`
	InputAssetIDs           []uuid.UUID `json:"input_asset_ids"`
	ModelID                 string      `json:"model_id"`
	ModelVersion            string      `json:"model_version"`
	ConfigVersion           string      `json:"config_version"`
	ForecastContractVersion string      `json:"forecast_contract_version"`
	BaselineModels          []string    `json:"baseline_models"`
}

type ForecastBaselineReady struct {
	SchemaVersion string                       `json:"schema_version"`
	EventID       uuid.UUID                    `json:"event_id"`
	EventType     string                       `json:"event_type"`
	OccurredAt    time.Time                    `json:"occurred_at"`
	RunID         uuid.UUID                    `json:"run_id"`
	JobID         uuid.UUID                    `json:"job_id"`
	TraceID       uuid.UUID                    `json:"trace_id"`
	Payload       ForecastBaselineReadyPayload `json:"payload"`
}

type ForecastBaselineReadyPayload struct {
	ForecastURI  string    `json:"forecast_uri"`
	IssueTime    time.Time `json:"issue_time"`
	GridID       string    `json:"grid_id"`
	ModelID      string    `json:"model_id"`
	ModelVersion string    `json:"model_version"`
	Config       string    `json:"config_version"`
	LeadCount    int       `json:"lead_count"`
	LeadStep     int       `json:"lead_step_minutes"`
	ValidFrom    time.Time `json:"valid_from"`
	ValidTo      time.Time `json:"valid_to"`
}

type ProductIDs struct {
	RainRate        uuid.UUID `json:"rain_rate"`
	Accumulation60  uuid.UUID `json:"accumulation_60"`
	Accumulation120 uuid.UUID `json:"accumulation_120"`
}

type ProductBuildRequested struct {
	SchemaVersion string                       `json:"schema_version"`
	EventID       uuid.UUID                    `json:"event_id"`
	EventType     string                       `json:"event_type"`
	OccurredAt    time.Time                    `json:"occurred_at"`
	RunID         uuid.UUID                    `json:"run_id"`
	JobID         uuid.UUID                    `json:"job_id"`
	TraceID       uuid.UUID                    `json:"trace_id"`
	Payload       ProductBuildRequestedPayload `json:"payload"`
}

type ProductBuildRequestedPayload struct {
	InputURI              string     `json:"input_uri"`
	InputSHA256           string     `json:"input_sha256"`
	OutputPrefix          string     `json:"output_prefix"`
	ModelRunID            uuid.UUID  `json:"model_run_id"`
	IssueTime             time.Time  `json:"issue_time"`
	GridID                string     `json:"grid_id"`
	ModelID               string     `json:"model_id"`
	ModelVersion          string     `json:"model_version"`
	ModelConfigVersion    string     `json:"model_config_version"`
	ProductConfigVersion  string     `json:"product_config_version"`
	ProductBundleContract string     `json:"product_bundle_contract_version"`
	ProductIDs            ProductIDs `json:"product_ids"`
}

type ProductPublished struct {
	SchemaVersion string                  `json:"schema_version"`
	EventID       uuid.UUID               `json:"event_id"`
	EventType     string                  `json:"event_type"`
	OccurredAt    time.Time               `json:"occurred_at"`
	RunID         uuid.UUID               `json:"run_id"`
	JobID         uuid.UUID               `json:"job_id"`
	TraceID       uuid.UUID               `json:"trace_id"`
	Payload       ProductPublishedPayload `json:"payload"`
}

type ProductPublishedPayload struct {
	ProductID    uuid.UUID               `json:"product_id"`
	ProductType  workflow.ProductType    `json:"product_type"`
	ModelID      string                  `json:"model_id"`
	ModelVersion string                  `json:"model_version"`
	Config       string                  `json:"config_version"`
	GridID       string                  `json:"grid_id"`
	IssueTime    time.Time               `json:"issue_time"`
	ValidTimes   []time.Time             `json:"valid_times"`
	Assets       []ProductPublishedAsset `json:"assets"`
}

type ProductPublishedAsset struct {
	AssetID    uuid.UUID `json:"asset_id"`
	AssetType  string    `json:"asset_type"`
	URI        string    `json:"uri"`
	SHA256     string    `json:"sha256"`
	SizeBytes  int64     `json:"size_bytes"`
	MediaType  string    `json:"media_type,omitempty"`
	LeadMinute *int      `json:"lead_time_minutes,omitempty"`
}

type ForecastVerificationRequested struct {
	SchemaVersion string                               `json:"schema_version"`
	EventID       uuid.UUID                            `json:"event_id"`
	EventType     string                               `json:"event_type"`
	OccurredAt    time.Time                            `json:"occurred_at"`
	RunID         uuid.UUID                            `json:"run_id"`
	JobID         uuid.UUID                            `json:"job_id"`
	TraceID       uuid.UUID                            `json:"trace_id"`
	Payload       ForecastVerificationRequestedPayload `json:"payload"`
}

type ForecastVerificationRequestedTruth struct {
	AnalysisID  uuid.UUID `json:"analysis_id"`
	ValidTime   time.Time `json:"valid_time"`
	InputURI    string    `json:"input_uri"`
	InputSHA256 string    `json:"input_sha256"`
}

type ForecastVerificationRequestedPayload struct {
	ForecastURI               string                               `json:"forecast_uri"`
	ForecastSHA256            string                               `json:"forecast_sha256"`
	TruthFrames               []ForecastVerificationRequestedTruth `json:"truth_frames"`
	OutputPrefix              string                               `json:"output_prefix"`
	IssueTime                 time.Time                            `json:"issue_time"`
	GridID                    string                               `json:"grid_id"`
	ModelID                   string                               `json:"model_id"`
	ModelVersion              string                               `json:"model_version"`
	ForecastContractVersion   string                               `json:"forecast_contract_version"`
	VerificationConfigVersion string                               `json:"verification_config_version"`
	ResultContractVersion     string                               `json:"result_contract_version"`
}

type JobCompleted struct {
	SchemaVersion string              `json:"schema_version"`
	EventID       uuid.UUID           `json:"event_id"`
	EventType     string              `json:"event_type"`
	OccurredAt    time.Time           `json:"occurred_at"`
	RunID         uuid.UUID           `json:"run_id"`
	JobID         uuid.UUID           `json:"job_id"`
	TraceID       uuid.UUID           `json:"trace_id"`
	Payload       JobCompletedPayload `json:"payload"`
}

type JobCompletedPayload struct {
	Status      string                     `json:"status"`
	StartedAt   time.Time                  `json:"started_at"`
	FinishedAt  time.Time                  `json:"finished_at"`
	RuntimeMS   int64                      `json:"runtime_ms"`
	Assets      []JobCompletedAsset        `json:"assets"`
	Metrics     map[string]float64         `json:"metrics"`
	Diagnostics map[string]json.RawMessage `json:"diagnostics,omitempty"`
}

type JobCompletedAsset struct {
	AssetType string `json:"asset_type"`
	URI       string `json:"uri"`
	SHA256    string `json:"sha256"`
	SizeBytes int64  `json:"size_bytes"`
	MediaType string `json:"media_type,omitempty"`
}

type JobFailed struct {
	SchemaVersion string           `json:"schema_version"`
	EventID       uuid.UUID        `json:"event_id"`
	EventType     string           `json:"event_type"`
	OccurredAt    time.Time        `json:"occurred_at"`
	RunID         uuid.UUID        `json:"run_id"`
	JobID         uuid.UUID        `json:"job_id"`
	TraceID       uuid.UUID        `json:"trace_id"`
	Payload       JobFailedPayload `json:"payload"`
}

type JobFailedPayload struct {
	Status       string         `json:"status"`
	StartedAt    time.Time      `json:"started_at"`
	FinishedAt   time.Time      `json:"finished_at"`
	RuntimeMS    int64          `json:"runtime_ms"`
	ErrorCode    string         `json:"error_code"`
	ErrorMessage string         `json:"error_message"`
	Retryable    bool           `json:"retryable"`
	Details      map[string]any `json:"details"`
}

var sha256Pattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var ErrInvalidEvent = errors.New("invalid event")

func DecodeJobCompleted(data []byte) (JobCompleted, error) {
	var event JobCompleted
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&event); err != nil {
		return JobCompleted{}, fmt.Errorf("%w: decode job.completed: %v", ErrInvalidEvent, err)
	}
	if err := ensureJSONEOF(decoder); err != nil {
		return JobCompleted{}, fmt.Errorf("%w: %v", ErrInvalidEvent, err)
	}
	if err := event.Validate(); err != nil {
		return JobCompleted{}, fmt.Errorf("%w: %v", ErrInvalidEvent, err)
	}
	return event, nil
}

func (event JobCompleted) Validate() error {
	if event.SchemaVersion != SchemaVersion || event.EventType != JobCompletedEventType {
		return fmt.Errorf("unsupported completion event contract")
	}
	if event.EventID == uuid.Nil || event.RunID == uuid.Nil || event.JobID == uuid.Nil || event.TraceID == uuid.Nil {
		return fmt.Errorf("completion event identifiers are required")
	}
	if event.OccurredAt.IsZero() || event.Payload.StartedAt.IsZero() || event.Payload.FinishedAt.IsZero() {
		return fmt.Errorf("completion event timestamps are required")
	}
	if event.Payload.Status != "succeeded" {
		return fmt.Errorf("unsupported completion status %q", event.Payload.Status)
	}
	if event.Payload.RuntimeMS < 0 || event.Payload.FinishedAt.Before(event.Payload.StartedAt) {
		return fmt.Errorf("invalid completion runtime")
	}
	for _, asset := range event.Payload.Assets {
		parsed, err := url.ParseRequestURI(asset.URI)
		if err != nil || parsed.Scheme == "" || asset.AssetType == "" || !sha256Pattern.MatchString(asset.SHA256) || asset.SizeBytes < 0 {
			return fmt.Errorf("invalid completion asset")
		}
	}
	return nil
}

func DecodeJobFailed(data []byte) (JobFailed, error) {
	var event JobFailed
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&event); err != nil {
		return JobFailed{}, fmt.Errorf("%w: decode job.failed: %v", ErrInvalidEvent, err)
	}
	if err := ensureJSONEOF(decoder); err != nil {
		return JobFailed{}, fmt.Errorf("%w: %v", ErrInvalidEvent, err)
	}
	if err := event.Validate(); err != nil {
		return JobFailed{}, fmt.Errorf("%w: %v", ErrInvalidEvent, err)
	}
	return event, nil
}

func (event JobFailed) Validate() error {
	if event.SchemaVersion != SchemaVersion || event.EventType != JobFailedEventType {
		return fmt.Errorf("unsupported failure event contract")
	}
	if event.EventID == uuid.Nil || event.RunID == uuid.Nil || event.JobID == uuid.Nil || event.TraceID == uuid.Nil {
		return fmt.Errorf("failure event identifiers are required")
	}
	if event.OccurredAt.IsZero() || event.Payload.StartedAt.IsZero() || event.Payload.FinishedAt.IsZero() {
		return fmt.Errorf("failure event timestamps are required")
	}
	if event.Payload.Status != "failed" {
		return fmt.Errorf("unsupported failure status %q", event.Payload.Status)
	}
	if event.Payload.RuntimeMS < 0 || event.Payload.FinishedAt.Before(event.Payload.StartedAt) {
		return fmt.Errorf("invalid failure runtime")
	}
	if len(event.Payload.ErrorCode) == 0 || len(event.Payload.ErrorCode) > 128 {
		return fmt.Errorf("invalid failure error code")
	}
	if len(event.Payload.ErrorMessage) == 0 || len(event.Payload.ErrorMessage) > 2048 {
		return fmt.Errorf("invalid failure error message")
	}
	if event.Payload.Details == nil {
		return fmt.Errorf("failure details are required")
	}
	return nil
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); err == io.EOF {
		return nil
	} else if err != nil {
		return fmt.Errorf("decode trailing completion data: %w", err)
	}
	return fmt.Errorf("completion event contains trailing JSON")
}

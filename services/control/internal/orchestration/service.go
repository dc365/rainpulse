package orchestration

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/url"
	"sort"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

type Repository interface {
	CreateBundle(context.Context, workflow.CreateBundle) error
	CreateRadarDecodeBundle(context.Context, workflow.RadarDecodeBundle) error
	CreateRadarQCBundle(context.Context, workflow.RadarQCBundle) error
	CreateRadarGridBundle(context.Context, workflow.RadarGridBundle) error
	CreateAnalysisMosaicBundle(context.Context, workflow.AnalysisMosaicBundle) error
	CreateAnalysisQPEBundle(context.Context, workflow.AnalysisQPEBundle) error
	CreateAnalysisDiagnosticsBundle(context.Context, workflow.AnalysisDiagnosticsBundle) error
	CreateNowcastInputBundle(context.Context, workflow.NowcastInputBundle) error
	CreatePystepsLKBundle(context.Context, workflow.PystepsLKBundle) error
	CreateProductBuildBundle(context.Context, workflow.ProductBuildBundle) error
	CreateForecastVerificationBundle(context.Context, workflow.ForecastVerificationBundle) error
	CreateDomainSimulation(context.Context, workflow.DomainSimulation) error
	GetRun(context.Context, uuid.UUID) (workflow.Run, error)
	GetJob(context.Context, uuid.UUID) (workflow.Job, error)
	ListJobs(context.Context, uuid.UUID) ([]workflow.Job, error)
	ClaimOutbox(context.Context) (workflow.OutboxEvent, error)
	MarkOutboxPublished(context.Context, uuid.UUID) error
	MarkOutboxFailed(context.Context, uuid.UUID, string) error
	ApplyCompletion(context.Context, JobCompleted, json.RawMessage) (bool, error)
	ApplyFailure(context.Context, JobFailed, json.RawMessage) (bool, error)
}

type RadarDecodeInput struct {
	RadarID         string
	DisplayName     *string
	Lifecycle       workflow.RadarLifecycle
	ConfigVersion   string
	Config          json.RawMessage
	ConfigSHA256    string
	SourceFormat    string
	InputURI        string
	InputSHA256     string
	InputSizeBytes  int64
	VolumeStartTime time.Time
	VolumeEndTime   time.Time
}

type RadarQCInput struct {
	ScanID                uuid.UUID
	RunID                 uuid.UUID
	RadarID               string
	RadarConfigVersion    string
	NormalizedURI         string
	CurrentStatus         workflow.RadarScanStatus
	Health                workflow.RadarHealthState
	QCProfile             string
	QCPipelineVersion     string
	FlagDefinitionVersion string
	QCConfig              json.RawMessage
	QCConfigSHA256        string
}

type RadarGridInput struct {
	ScanID             uuid.UUID
	RunID              uuid.UUID
	RadarID            string
	QCURI              string
	CurrentStatus      workflow.RadarScanStatus
	GridID             string
	GridConfigVersion  string
	GridProfileVersion string
	HybridScanVersion  string
	GridConfig         json.RawMessage
	GridConfigSHA256   string
}

type AnalysisMosaicCandidate struct {
	RadarID           string
	ScanID            uuid.UUID
	GridURI           string
	VolumeEndTime     time.Time
	CurrentStatus     workflow.RadarScanStatus
	HybridScanVersion string
}

type AnalysisMosaicInput struct {
	AnalysisTime           time.Time
	GridID                 string
	GridConfigVersion      string
	MosaicConfigVersion    string
	MosaicAlgorithmVersion string
	FlagDefinitionVersion  string
	MaximumAbsoluteOffset  time.Duration
	MinimumContributors    int
	ExpectedRadarIDs       []string
	Candidates             []AnalysisMosaicCandidate
	MosaicConfig           json.RawMessage
	MosaicConfigSHA256     string
}

type AnalysisQPEInput struct {
	AnalysisID             uuid.UUID
	RunID                  uuid.UUID
	AnalysisTime           time.Time
	GridID                 string
	GridConfigVersion      string
	MosaicConfigVersion    string
	MosaicAlgorithmVersion string
	FlagDefinitionVersion  string
	MosaicURI              string
	CurrentStatus          workflow.AnalysisStatus
	QPEConfigVersion       string
	QPEAlgorithmVersion    string
	QPEConfig              json.RawMessage
	QPEConfigSHA256        string
}

type AnalysisDiagnosticsInput struct {
	AnalysisID              uuid.UUID
	RunID                   uuid.UUID
	AnalysisTime            time.Time
	GridID                  string
	AnalysisURI             string
	CurrentStatus           workflow.AnalysisStatus
	RadarInputs             []workflow.AnalysisDiagnosticRadarInput
	DiagnosticConfig        json.RawMessage
	DiagnosticConfigSHA256  string
	DiagnosticConfigVersion string
	RendererVersion         string
	FlagDefinitionVersion   string
}

type NowcastInputCandidate struct {
	AnalysisID          uuid.UUID
	AnalysisTime        time.Time
	GridID              string
	AnalysisURI         string
	CurrentStatus       workflow.AnalysisStatus
	OperationalEligible bool
	ValidCoverageRatio  float64
	MeanQualityIndex    float64
}

type NowcastInputInput struct {
	IssueTime                           time.Time
	GridID                              string
	GridConfigVersion                   string
	PreprocessVersion                   string
	GateConfigVersion                   string
	ExecutionMode                       string
	RequireAllFramesOperationalEligible bool
	MinimumFrames                       int
	MaximumFrames                       int
	Timestep                            time.Duration
	MinimumValidCoverageRatio           float64
	MinimumMeanQualityIndex             float64
	Candidates                          []NowcastInputCandidate
	Config                              json.RawMessage
	ConfigSHA256                        string
}

type PystepsLKInput struct {
	RunID                   uuid.UUID
	NowcastInputJobID       uuid.UUID
	IssueTime               time.Time
	GridID                  string
	CurrentStatus           workflow.RunStatus
	InputURI                string
	InputAssetIDs           []uuid.UUID
	ModelID                 string
	ModelVersion            string
	ConfigVersion           string
	ForecastContractVersion string
	BaselineModels          []string
	Config                  json.RawMessage
	ConfigSHA256            string
}

type ProductBuildInput struct {
	RunID                 uuid.UUID
	ModelRunID            uuid.UUID
	IssueTime             time.Time
	GridID                string
	CurrentStatus         workflow.RunStatus
	ForecastURI           string
	ForecastSHA256        string
	InputAssetIDs         []uuid.UUID
	ModelID               string
	ModelVersion          string
	ModelConfigVersion    string
	ProductConfigVersion  string
	ProductBundleContract string
	ProductConfig         json.RawMessage
	ProductConfigSHA256   string
}

type ForecastVerificationTruth = workflow.ForecastVerificationTruth

type ForecastVerificationInput struct {
	RunID                     uuid.UUID
	IssueTime                 time.Time
	GridID                    string
	CurrentStatus             workflow.RunStatus
	ForecastURI               string
	ForecastSHA256            string
	ModelID                   string
	ModelVersion              string
	ForecastContractVersion   string
	Truth                     []ForecastVerificationTruth
	VerificationConfigVersion string
	ResultContractVersion     string
	VerificationConfig        json.RawMessage
	VerificationConfigSHA256  string
}

type Publisher interface {
	Publish(context.Context, workflow.OutboxEvent) error
}

type Options struct {
	Now   func() time.Time
	NewID func() uuid.UUID
}

type Service struct {
	repository Repository
	now        func() time.Time
	newID      func() uuid.UUID
}

var ErrUnsupportedRerun = errors.New("forecast rerun is unsupported for this run type")

func NewService(repository Repository, options Options) *Service {
	now := options.Now
	if now == nil {
		now = time.Now
	}
	newID := options.NewID
	if newID == nil {
		newID = uuid.New
	}
	return &Service{repository: repository, now: now, newID: newID}
}

func (service *Service) CreateRadarDecode(
	ctx context.Context,
	input RadarDecodeInput,
) (workflow.RadarScan, workflow.Job, error) {
	if err := validateRadarDecodeInput(input); err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	now := service.now().UTC()
	start := input.VolumeStartTime.UTC()
	end := input.VolumeEndTime.UTC()
	assetID := stableID("radar-asset", input.InputSHA256)
	scanID := stableID("radar-scan", input.RadarID, start.Format(time.RFC3339Nano), end.Format(time.RFC3339Nano))
	runID := stableID("radar-scan-run", scanID.String(), input.ConfigVersion, RadarDecoderVersion)
	jobID := stableID("radar-decode-job", runID.String())
	traceID := stableID("radar-decode-trace", runID.String())
	eventID := stableID("radar-decode-request", jobID.String())
	sourceID := stableID("radar-source", input.RadarID)
	outputPrefix := fmt.Sprintf("s3://rainpulse/radar/normalized/%s/%s/", input.RadarID, scanID)

	request := RadarDecodeRequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID,
		EventType:     RadarDecodeRequestedEventType,
		OccurredAt:    now,
		RunID:         runID,
		JobID:         jobID,
		TraceID:       traceID,
		Payload: RadarDecodeRequestedPayload{
			ScanID: scanID, AssetID: assetID, RadarID: input.RadarID,
			InputURI: input.InputURI, InputSHA256: input.InputSHA256,
			InputSizeBytes: input.InputSizeBytes, OutputPrefix: outputPrefix,
			SourceFormat: input.SourceFormat, RadarConfig: input.ConfigVersion,
			DecoderVersion: RadarDecoderVersion,
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("encode radar decode request: %w", err)
	}
	metadata, err := json.Marshal(map[string]any{
		"radar_id": input.RadarID, "source_format": input.SourceFormat,
		"volume_start_time": start, "volume_end_time": end,
	})
	if err != nil {
		return workflow.RadarScan{}, workflow.Job{}, fmt.Errorf("encode radar asset metadata: %w", err)
	}
	scan := workflow.RadarScan{
		ID: scanID, RunID: runID, RadarID: input.RadarID,
		VolumeStartTime: start, VolumeEndTime: end, ReceivedAt: now,
		RadarConfigVersion: input.ConfigVersion,
		Status:             workflow.RadarScanRawValidating, CreatedAt: now, UpdatedAt: now,
	}
	job := workflow.Job{
		ID: jobID, RunID: runID, TraceID: traceID, JobType: RadarDecodeJobType,
		ConfigVersion: input.ConfigVersion, Status: workflow.JobPending, Attempt: 1,
		RequestPayload: payload, CreatedAt: now,
	}
	bundle := workflow.RadarDecodeBundle{
		Radar: workflow.Radar{
			ID: input.RadarID, DisplayName: input.DisplayName, Lifecycle: input.Lifecycle,
			ConfigVersion: input.ConfigVersion, CreatedAt: now, UpdatedAt: now,
		},
		Config: input.Config, ConfigSHA256: input.ConfigSHA256,
		Asset: workflow.RawRadarAsset{
			ID: assetID, SourceID: sourceID, ObservedAt: start, ObjectURI: input.InputURI,
			MediaType: "application/x-bzip2", SizeBytes: input.InputSizeBytes,
			SHA256: input.InputSHA256, Metadata: metadata,
		},
		Scan: scan,
		Job:  job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(), EventType: RadarDecodeRequestedEventType,
			Subject: RadarDecodeRequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreateRadarDecodeBundle(ctx, bundle); err != nil {
		return workflow.RadarScan{}, workflow.Job{}, err
	}
	return scan, job, nil
}

func validateRadarDecodeInput(input RadarDecodeInput) error {
	if input.RadarID == "" || input.ConfigVersion == "" || input.SourceFormat == "" {
		return fmt.Errorf("radar ID, config version and source format are required")
	}
	if input.Lifecycle != workflow.RadarDraft && input.Lifecycle != workflow.RadarReady && input.Lifecycle != workflow.RadarDisabled {
		return fmt.Errorf("unsupported radar lifecycle %q", input.Lifecycle)
	}
	if len(input.Config) == 0 || !json.Valid(input.Config) {
		return fmt.Errorf("radar configuration must be valid JSON")
	}
	if !sha256Pattern.MatchString(input.ConfigSHA256) || !sha256Pattern.MatchString(input.InputSHA256) {
		return fmt.Errorf("radar configuration and input SHA-256 values are required")
	}
	parsed, err := url.ParseRequestURI(input.InputURI)
	expectedPrefix := "/radar/raw/" + input.RadarID + "/"
	if err != nil || parsed.Scheme != "s3" || parsed.Host == "" || !strings.HasPrefix(parsed.Path, expectedPrefix) {
		return fmt.Errorf("radar decoder input must be an immutable radar raw-archive URI")
	}
	if input.InputSizeBytes <= 0 || input.VolumeStartTime.IsZero() || input.VolumeEndTime.Before(input.VolumeStartTime) {
		return fmt.Errorf("invalid radar input size or volume time range")
	}
	return nil
}

func (service *Service) CreateRadarQC(
	ctx context.Context,
	input RadarQCInput,
) (workflow.Job, error) {
	if err := validateRadarQCInput(input); err != nil {
		return workflow.Job{}, err
	}
	now := service.now().UTC()
	jobID := stableID("radar-qc-job", input.RunID.String(), input.QCPipelineVersion)
	traceID := stableID("radar-qc-trace", input.RunID.String(), input.QCPipelineVersion)
	eventID := stableID("radar-qc-request", jobID.String())
	outputPrefix := fmt.Sprintf(
		"s3://rainpulse/radar/qc/%s/%s/%s/",
		input.RadarID,
		input.ScanID,
		url.PathEscape(input.QCPipelineVersion),
	)
	request := RadarQCRequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID,
		EventType:     RadarQCRequestedEventType,
		OccurredAt:    now,
		RunID:         input.RunID,
		JobID:         jobID,
		TraceID:       traceID,
		Payload: RadarQCRequestedPayload{
			ScanID: input.ScanID, RadarID: input.RadarID,
			InputURI: input.NormalizedURI, OutputPrefix: outputPrefix,
			RadarConfig: input.RadarConfigVersion, QCProfile: input.QCProfile,
			QCPipelineVersion:     input.QCPipelineVersion,
			FlagDefinitionVersion: input.FlagDefinitionVersion,
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.Job{}, fmt.Errorf("encode radar QC request: %w", err)
	}
	job := workflow.Job{
		ID: jobID, RunID: input.RunID, TraceID: traceID, JobType: RadarQCJobType,
		ConfigVersion: input.QCPipelineVersion, Status: workflow.JobPending, Attempt: 1,
		RequestPayload: payload, CreatedAt: now,
	}
	bundle := workflow.RadarQCBundle{
		ScanID:       input.ScanID,
		Status:       input.CurrentStatus,
		Config:       input.QCConfig,
		ConfigSHA256: input.QCConfigSHA256,
		Job:          job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(), EventType: RadarQCRequestedEventType,
			Subject: RadarQCRequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreateRadarQCBundle(ctx, bundle); err != nil {
		return workflow.Job{}, err
	}
	return job, nil
}

func validateRadarQCInput(input RadarQCInput) error {
	if input.ScanID == uuid.Nil || input.RunID == uuid.Nil || input.RadarID == "" ||
		input.RadarConfigVersion == "" || input.QCProfile == "" ||
		input.QCPipelineVersion == "" || input.FlagDefinitionVersion == "" {
		return fmt.Errorf("radar QC identity and version fields are required")
	}
	if len(input.QCConfig) == 0 || !json.Valid(input.QCConfig) ||
		!sha256Pattern.MatchString(input.QCConfigSHA256) {
		return fmt.Errorf("radar QC configuration and SHA-256 are required")
	}
	if input.CurrentStatus != workflow.RadarScanNormalized &&
		input.CurrentStatus != workflow.RadarScanQCRunning &&
		input.CurrentStatus != workflow.RadarScanQCReady &&
		input.CurrentStatus != workflow.RadarScanGridReady &&
		input.CurrentStatus != workflow.RadarScanFailed {
		return fmt.Errorf("radar scan status %q cannot enter QC", input.CurrentStatus)
	}
	if input.Health == workflow.RadarHealthUnavailable || input.Health == workflow.RadarHealthUnknown {
		return fmt.Errorf("radar health %q cannot enter QC", input.Health)
	}
	parsed, err := url.ParseRequestURI(input.NormalizedURI)
	if err != nil || parsed.Scheme != "s3" {
		return fmt.Errorf("radar QC input must be an s3 URI")
	}
	return nil
}

func (service *Service) CreateRadarGrid(
	ctx context.Context,
	input RadarGridInput,
) (workflow.Job, error) {
	if err := validateRadarGridInput(input); err != nil {
		return workflow.Job{}, err
	}
	now := service.now().UTC()
	jobID := stableID("radar-grid-job", input.RunID.String(), input.HybridScanVersion)
	traceID := stableID("radar-grid-trace", input.RunID.String(), input.HybridScanVersion)
	eventID := stableID("radar-grid-request", jobID.String())
	outputPrefix := fmt.Sprintf(
		"s3://rainpulse/radar/grid/%s/%s/%s/",
		input.RadarID,
		input.ScanID,
		url.PathEscape(input.HybridScanVersion),
	)
	request := RadarGridRequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID,
		EventType:     RadarGridRequestedEventType,
		OccurredAt:    now,
		RunID:         input.RunID,
		JobID:         jobID,
		TraceID:       traceID,
		Payload: RadarGridRequestedPayload{
			ScanID: input.ScanID, RadarID: input.RadarID,
			InputURI: input.QCURI, OutputPrefix: outputPrefix,
			GridID: input.GridID, GridConfig: input.GridConfigVersion,
			HybridScanVersion: input.HybridScanVersion,
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.Job{}, fmt.Errorf("encode radar grid request: %w", err)
	}
	job := workflow.Job{
		ID: jobID, RunID: input.RunID, TraceID: traceID, JobType: RadarGridJobType,
		ConfigVersion: input.GridProfileVersion, Status: workflow.JobPending, Attempt: 1,
		RequestPayload: payload, CreatedAt: now,
	}
	bundle := workflow.RadarGridBundle{
		ScanID: input.ScanID, Status: input.CurrentStatus,
		Config: input.GridConfig, ConfigSHA256: input.GridConfigSHA256,
		Job: job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(), EventType: RadarGridRequestedEventType,
			Subject: RadarGridRequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreateRadarGridBundle(ctx, bundle); err != nil {
		return workflow.Job{}, err
	}
	return job, nil
}

func validateRadarGridInput(input RadarGridInput) error {
	if input.ScanID == uuid.Nil || input.RunID == uuid.Nil || input.RadarID == "" ||
		input.GridID == "" || input.GridConfigVersion == "" ||
		input.GridProfileVersion == "" || input.HybridScanVersion == "" {
		return fmt.Errorf("radar grid identity and version fields are required")
	}
	if len(input.GridConfig) == 0 || !json.Valid(input.GridConfig) ||
		!sha256Pattern.MatchString(input.GridConfigSHA256) {
		return fmt.Errorf("radar grid configuration and SHA-256 are required")
	}
	if input.CurrentStatus != workflow.RadarScanQCReady &&
		input.CurrentStatus != workflow.RadarScanGridRunning &&
		input.CurrentStatus != workflow.RadarScanGridReady &&
		input.CurrentStatus != workflow.RadarScanFailed {
		return fmt.Errorf("radar scan status %q cannot enter gridding", input.CurrentStatus)
	}
	parsed, err := url.ParseRequestURI(input.QCURI)
	if err != nil || parsed.Scheme != "s3" {
		return fmt.Errorf("radar grid input must be an s3 URI")
	}
	return nil
}

func (service *Service) CreateAnalysisMosaic(
	ctx context.Context,
	input AnalysisMosaicInput,
) (workflow.AnalysisCycle, workflow.Job, error) {
	if err := validateAnalysisMosaicInput(input); err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, err
	}
	analysisTime := input.AnalysisTime.UTC()
	selected, radars := alignMosaicCandidates(input, analysisTime)
	if len(selected) < input.MinimumContributors {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf(
			"aligned radar contributors %d are below configured minimum %d",
			len(selected), input.MinimumContributors,
		)
	}
	now := service.now().UTC()
	identity := []string{
		analysisTime.Format(time.RFC3339), input.GridID, input.GridConfigVersion,
		input.MosaicConfigVersion, input.MosaicAlgorithmVersion,
	}
	analysisID := stableID(append([]string{"analysis"}, identity...)...)
	runID := stableID(append([]string{"analysis-run"}, identity...)...)
	jobID := stableID("analysis-mosaic-job", runID.String(), input.MosaicAlgorithmVersion)
	traceID := stableID("analysis-mosaic-trace", runID.String(), input.MosaicAlgorithmVersion)
	eventID := stableID("analysis-mosaic-request", jobID.String())
	outputPrefix := fmt.Sprintf(
		"s3://rainpulse/analysis/mosaic/%s/%s/%s/%s/",
		input.GridID,
		analysisTime.Format("2006/01/02/150405Z"),
		url.PathEscape(input.MosaicAlgorithmVersion),
		analysisID.String(),
	)
	requestInputs := make([]AnalysisMosaicRequestedInput, 0, len(selected))
	for _, candidate := range selected {
		requestInputs = append(requestInputs, AnalysisMosaicRequestedInput{
			RadarID: candidate.RadarID, ScanID: candidate.ScanID,
			GridURI: candidate.GridURI,
			TimeOffsetSeconds: int(math.Round(
				candidate.VolumeEndTime.UTC().Sub(analysisTime).Seconds(),
			)),
			HybridScanVersion: candidate.HybridScanVersion,
		})
	}
	request := AnalysisMosaicRequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID, EventType: AnalysisMosaicRequestedEventType,
		OccurredAt: now, RunID: runID, JobID: jobID, TraceID: traceID,
		Payload: AnalysisMosaicRequestedPayload{
			AnalysisID: analysisID, AnalysisTime: analysisTime,
			GridID: input.GridID, GridConfigVersion: input.GridConfigVersion,
			Inputs: requestInputs, OutputPrefix: outputPrefix,
			MosaicConfigVersion: input.MosaicConfigVersion,
			MosaicAlgorithm:     input.MosaicAlgorithmVersion,
			FlagDefinition:      input.FlagDefinitionVersion,
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, fmt.Errorf(
			"encode analysis mosaic request: %w", err,
		)
	}
	job := workflow.Job{
		ID: jobID, RunID: runID, TraceID: traceID, JobType: AnalysisMosaicJobType,
		ConfigVersion: input.MosaicConfigVersion, Status: workflow.JobPending,
		Attempt: 1, RequestPayload: payload, CreatedAt: now,
	}
	analysis := workflow.AnalysisCycle{
		ID: analysisID, RunID: runID, AnalysisTime: analysisTime,
		GridID: input.GridID, ConfigVersion: input.MosaicConfigVersion,
		Status: workflow.AnalysisMosaic, RadarCount: len(selected), Radars: radars,
		CreatedAt: now, UpdatedAt: now,
	}
	bundle := workflow.AnalysisMosaicBundle{
		Analysis: analysis, AlgorithmVersion: input.MosaicAlgorithmVersion,
		Config:       input.MosaicConfig,
		ConfigSHA256: input.MosaicConfigSHA256, Job: job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(),
			EventType: AnalysisMosaicRequestedEventType,
			Subject:   AnalysisMosaicRequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreateAnalysisMosaicBundle(ctx, bundle); err != nil {
		return workflow.AnalysisCycle{}, workflow.Job{}, err
	}
	return analysis, job, nil
}

func validateAnalysisMosaicInput(input AnalysisMosaicInput) error {
	if input.AnalysisTime.IsZero() || input.GridID == "" || input.GridConfigVersion == "" ||
		input.MosaicConfigVersion == "" || input.MosaicAlgorithmVersion == "" ||
		input.FlagDefinitionVersion == "" {
		return fmt.Errorf("analysis mosaic identity and version fields are required")
	}
	analysisTime := input.AnalysisTime.UTC()
	if !analysisTime.Equal(analysisTime.Truncate(5 * time.Minute)) {
		return fmt.Errorf("analysis time must be on a five-minute UTC boundary")
	}
	if input.MaximumAbsoluteOffset <= 0 || input.MinimumContributors <= 0 {
		return fmt.Errorf("analysis alignment tolerance and contributor minimum are required")
	}
	if len(input.MosaicConfig) == 0 || !json.Valid(input.MosaicConfig) ||
		!sha256Pattern.MatchString(input.MosaicConfigSHA256) {
		return fmt.Errorf("mosaic configuration and SHA-256 are required")
	}
	expected := make(map[string]struct{}, len(input.ExpectedRadarIDs))
	for _, radarID := range input.ExpectedRadarIDs {
		if radarID == "" {
			return fmt.Errorf("expected radar ID cannot be empty")
		}
		if _, exists := expected[radarID]; exists {
			return fmt.Errorf("expected radar IDs must be unique")
		}
		expected[radarID] = struct{}{}
	}
	scans := make(map[uuid.UUID]struct{}, len(input.Candidates))
	for _, candidate := range input.Candidates {
		if candidate.RadarID == "" || candidate.ScanID == uuid.Nil ||
			candidate.VolumeEndTime.IsZero() || candidate.HybridScanVersion == "" {
			return fmt.Errorf("mosaic candidate identity, time and version are required")
		}
		if _, exists := scans[candidate.ScanID]; exists {
			return fmt.Errorf("mosaic candidate scan IDs must be unique")
		}
		scans[candidate.ScanID] = struct{}{}
		parsed, err := url.ParseRequestURI(candidate.GridURI)
		if err != nil || parsed.Scheme != "s3" {
			return fmt.Errorf("mosaic candidate grid must be an s3 URI")
		}
	}
	return nil
}

func (service *Service) CreateAnalysisQPE(
	ctx context.Context,
	input AnalysisQPEInput,
) (workflow.Job, error) {
	if err := validateAnalysisQPEInput(input); err != nil {
		return workflow.Job{}, err
	}
	now := service.now().UTC()
	jobID := stableID("analysis-qpe-job", input.RunID.String(), input.QPEAlgorithmVersion)
	traceID := stableID("analysis-qpe-trace", input.RunID.String(), input.QPEAlgorithmVersion)
	eventID := stableID("analysis-qpe-request", jobID.String())
	outputPrefix := fmt.Sprintf(
		"s3://rainpulse/analysis/%s/%s/%s/%s/",
		input.GridID,
		input.AnalysisTime.UTC().Format("2006/01/02/150405Z"),
		url.PathEscape(input.QPEAlgorithmVersion),
		input.AnalysisID.String(),
	)
	request := AnalysisQPERequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID,
		EventType:     AnalysisQPERequestedEventType,
		OccurredAt:    now,
		RunID:         input.RunID,
		JobID:         jobID,
		TraceID:       traceID,
		Payload: AnalysisQPERequestedPayload{
			AnalysisID: input.AnalysisID, AnalysisTime: input.AnalysisTime.UTC(),
			GridID: input.GridID, GridConfigVersion: input.GridConfigVersion,
			InputURI: input.MosaicURI, OutputPrefix: outputPrefix,
			MosaicConfigVersion:   input.MosaicConfigVersion,
			MosaicAlgorithm:       input.MosaicAlgorithmVersion,
			QPEConfigVersion:      input.QPEConfigVersion,
			QPEAlgorithmVersion:   input.QPEAlgorithmVersion,
			FlagDefinitionVersion: input.FlagDefinitionVersion,
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.Job{}, fmt.Errorf("encode analysis QPE request: %w", err)
	}
	job := workflow.Job{
		ID: jobID, RunID: input.RunID, TraceID: traceID, JobType: AnalysisQPEJobType,
		ConfigVersion: input.QPEConfigVersion, Status: workflow.JobPending,
		Attempt: 1, RequestPayload: payload, CreatedAt: now,
	}
	bundle := workflow.AnalysisQPEBundle{
		AnalysisID: input.AnalysisID, RunID: input.RunID,
		CurrentStatus: input.CurrentStatus, MosaicURI: input.MosaicURI,
		ConfigVersion:    input.QPEConfigVersion,
		AlgorithmVersion: input.QPEAlgorithmVersion,
		Config:           input.QPEConfig, ConfigSHA256: input.QPEConfigSHA256,
		Job: job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(),
			EventType: AnalysisQPERequestedEventType,
			Subject:   AnalysisQPERequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreateAnalysisQPEBundle(ctx, bundle); err != nil {
		return workflow.Job{}, err
	}
	return job, nil
}

func validateAnalysisQPEInput(input AnalysisQPEInput) error {
	if input.AnalysisID == uuid.Nil || input.RunID == uuid.Nil ||
		input.AnalysisTime.IsZero() || input.GridID == "" ||
		input.GridConfigVersion == "" || input.MosaicConfigVersion == "" ||
		input.MosaicAlgorithmVersion == "" || input.FlagDefinitionVersion == "" ||
		input.QPEConfigVersion == "" || input.QPEAlgorithmVersion == "" {
		return fmt.Errorf("analysis QPE identity and version fields are required")
	}
	if !input.AnalysisTime.UTC().Equal(input.AnalysisTime.UTC().Truncate(5 * time.Minute)) {
		return fmt.Errorf("analysis QPE time must be on a five-minute UTC boundary")
	}
	if input.CurrentStatus != workflow.AnalysisQPE &&
		input.CurrentStatus != workflow.AnalysisReady {
		return fmt.Errorf("analysis status %q cannot enter QPE", input.CurrentStatus)
	}
	parsed, err := url.ParseRequestURI(input.MosaicURI)
	if err != nil || parsed.Scheme != "s3" {
		return fmt.Errorf("analysis QPE input must be an s3 URI")
	}
	if len(input.QPEConfig) == 0 || !json.Valid(input.QPEConfig) ||
		!sha256Pattern.MatchString(input.QPEConfigSHA256) {
		return fmt.Errorf("QPE configuration and SHA-256 are required")
	}
	return nil
}

func (service *Service) CreateAnalysisDiagnostics(
	ctx context.Context,
	input AnalysisDiagnosticsInput,
) (workflow.Job, error) {
	if err := validateAnalysisDiagnosticsInput(input); err != nil {
		return workflow.Job{}, err
	}
	now := service.now().UTC()
	jobID := stableID(
		"analysis-diagnostics-job",
		input.RunID.String(),
		input.RendererVersion,
	)
	traceID := stableID("analysis-diagnostics-trace", jobID.String())
	eventID := stableID("analysis-diagnostics-request", jobID.String())
	outputPrefix := fmt.Sprintf(
		"s3://rainpulse/diagnostics/%s/%s/",
		input.AnalysisID,
		url.PathEscape(input.RendererVersion),
	)
	request := AnalysisDiagnosticsRequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID,
		EventType:     AnalysisDiagnosticsRequestedEventType,
		OccurredAt:    now,
		RunID:         input.RunID,
		JobID:         jobID,
		TraceID:       traceID,
		Payload: AnalysisDiagnosticsRequestedPayload{
			AnalysisID:            input.AnalysisID,
			AnalysisTime:          input.AnalysisTime.UTC(),
			GridID:                input.GridID,
			InputURI:              input.AnalysisURI,
			RadarInputs:           input.RadarInputs,
			OutputPrefix:          outputPrefix,
			DiagnosticConfig:      input.DiagnosticConfigVersion,
			RendererVersion:       input.RendererVersion,
			FlagDefinitionVersion: input.FlagDefinitionVersion,
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.Job{}, fmt.Errorf("encode analysis diagnostics request: %w", err)
	}
	job := workflow.Job{
		ID: jobID, RunID: input.RunID, TraceID: traceID,
		JobType:       AnalysisDiagnosticsJobType,
		ConfigVersion: input.DiagnosticConfigVersion,
		Status:        workflow.JobPending, Attempt: 1,
		RequestPayload: payload, CreatedAt: now,
	}
	bundle := workflow.AnalysisDiagnosticsBundle{
		AnalysisID: input.AnalysisID, RunID: input.RunID,
		AnalysisURI:     input.AnalysisURI,
		ConfigVersion:   input.DiagnosticConfigVersion,
		RendererVersion: input.RendererVersion,
		Config:          input.DiagnosticConfig,
		ConfigSHA256:    input.DiagnosticConfigSHA256,
		RadarInputs:     input.RadarInputs,
		Job:             job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(),
			EventType: AnalysisDiagnosticsRequestedEventType,
			Subject:   AnalysisDiagnosticsRequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreateAnalysisDiagnosticsBundle(ctx, bundle); err != nil {
		return workflow.Job{}, err
	}
	return job, nil
}

func validateAnalysisDiagnosticsInput(input AnalysisDiagnosticsInput) error {
	if input.AnalysisID == uuid.Nil || input.RunID == uuid.Nil ||
		input.AnalysisTime.IsZero() || input.GridID == "" ||
		input.DiagnosticConfigVersion == "" || input.RendererVersion == "" ||
		input.FlagDefinitionVersion == "" {
		return fmt.Errorf("analysis diagnostic identity and version fields are required")
	}
	if input.CurrentStatus != workflow.AnalysisReady {
		return fmt.Errorf("analysis status %q cannot render diagnostics", input.CurrentStatus)
	}
	parsed, err := url.ParseRequestURI(input.AnalysisURI)
	if err != nil || parsed.Scheme != "s3" {
		return fmt.Errorf("diagnostic input must be a committed s3 RadarAnalysis URI")
	}
	if len(input.RadarInputs) == 0 {
		return fmt.Errorf("at least one contributing QC radar input is required")
	}
	radars := make(map[string]struct{}, len(input.RadarInputs))
	scans := make(map[uuid.UUID]struct{}, len(input.RadarInputs))
	for _, radar := range input.RadarInputs {
		uri, uriErr := url.ParseRequestURI(radar.QCURI)
		if radar.RadarID == "" || radar.ScanID == uuid.Nil || uriErr != nil || uri.Scheme != "s3" {
			return fmt.Errorf("diagnostic radar identity and QC s3 URI are required")
		}
		if _, exists := radars[radar.RadarID]; exists {
			return fmt.Errorf("diagnostic radar IDs must be unique")
		}
		if _, exists := scans[radar.ScanID]; exists {
			return fmt.Errorf("diagnostic scan IDs must be unique")
		}
		radars[radar.RadarID] = struct{}{}
		scans[radar.ScanID] = struct{}{}
	}
	if len(input.DiagnosticConfig) == 0 || !json.Valid(input.DiagnosticConfig) ||
		!sha256Pattern.MatchString(input.DiagnosticConfigSHA256) {
		return fmt.Errorf("diagnostic configuration and SHA-256 are required")
	}
	return nil
}

func (service *Service) CreateNowcastInput(
	ctx context.Context,
	input NowcastInputInput,
) (workflow.Run, workflow.Job, error) {
	input = normalizeNowcastInput(input)
	frames, err := validateAndSelectNowcastFrames(input)
	if err != nil {
		return workflow.Run{}, workflow.Job{}, err
	}
	now := service.now().UTC()
	issueTime := input.IssueTime.UTC()
	runID := stableID(
		"nowcast-input-run",
		input.GridID,
		issueTime.Format(time.RFC3339),
		input.PreprocessVersion,
		input.GateConfigVersion,
	)
	jobID := stableID("nowcast-input-job", runID.String())
	traceID := stableID("nowcast-input-trace", runID.String())
	eventID := stableID("nowcast-input-request", jobID.String())
	outputPrefix := fmt.Sprintf(
		"s3://rainpulse/nowcast-input/%s/%s/%s/%s/",
		input.GridID,
		issueTime.Format("2006/01/02/150405Z"),
		url.PathEscape(input.PreprocessVersion),
		runID.String(),
	)
	analysisIDs := make([]uuid.UUID, len(frames))
	inputURIs := make([]string, len(frames))
	for index, frame := range frames {
		analysisIDs[index] = frame.AnalysisID
		inputURIs[index] = frame.InputURI
	}
	request := NowcastInputRequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID,
		EventType:     NowcastInputRequestedEventType,
		OccurredAt:    now,
		RunID:         runID,
		JobID:         jobID,
		TraceID:       traceID,
		Payload: NowcastInputRequestedPayload{
			AnalysisIDs: analysisIDs, InputURIs: inputURIs,
			OutputPrefix: outputPrefix, IssueTime: issueTime, GridID: input.GridID,
			PreprocessVersion: input.PreprocessVersion,
			GateConfigVersion: input.GateConfigVersion,
			ExecutionMode:     input.ExecutionMode,
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("encode NowcastInput request: %w", err)
	}
	run := workflow.Run{
		ID: runID, IssueTime: issueTime, GridID: input.GridID,
		ConfigVersion: input.GateConfigVersion, Status: workflow.RunPreprocessing,
		CreatedAt: now, UpdatedAt: now,
	}
	job := workflow.Job{
		ID: jobID, RunID: runID, TraceID: traceID, JobType: NowcastInputJobType,
		ConfigVersion: input.GateConfigVersion, Status: workflow.JobPending,
		Attempt: 1, RequestPayload: payload, CreatedAt: now,
	}
	bundle := workflow.NowcastInputBundle{
		Run: run, Frames: frames,
		PreprocessVersion:                   input.PreprocessVersion,
		GateConfigVersion:                   input.GateConfigVersion,
		ExecutionMode:                       input.ExecutionMode,
		RequireAllFramesOperationalEligible: input.RequireAllFramesOperationalEligible,
		Config:                              input.Config, ConfigSHA256: input.ConfigSHA256,
		Job: job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(),
			EventType: NowcastInputRequestedEventType,
			Subject:   NowcastInputRequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreateNowcastInputBundle(ctx, bundle); err != nil {
		return workflow.Run{}, workflow.Job{}, err
	}
	return run, job, nil
}

func normalizeNowcastInput(input NowcastInputInput) NowcastInputInput {
	if input.ExecutionMode == "" {
		input.ExecutionMode = "operational"
		input.RequireAllFramesOperationalEligible = true
	}
	return input
}

func (service *Service) CreatePystepsLK(
	ctx context.Context,
	input PystepsLKInput,
) (workflow.Run, workflow.Job, error) {
	if err := validatePystepsLKInput(input); err != nil {
		return workflow.Run{}, workflow.Job{}, err
	}
	now := service.now().UTC()
	issueTime := input.IssueTime.UTC()
	jobID := stableID(
		"pysteps-lk-job", input.RunID.String(), input.ModelVersion, input.ConfigVersion,
	)
	traceID := stableID("pysteps-lk-trace", input.RunID.String())
	eventID := stableID("pysteps-lk-request", jobID.String())
	modelRunID := stableID("pysteps-lk-model-run", jobID.String())
	outputPrefix := fmt.Sprintf(
		"s3://rainpulse/products/%s/%s/%s/",
		input.RunID,
		url.PathEscape(input.ModelID),
		url.PathEscape(input.ModelVersion),
	)
	request := PystepsLKRequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID,
		EventType:     PystepsLKRequestedEventType,
		OccurredAt:    now,
		RunID:         input.RunID,
		JobID:         jobID,
		TraceID:       traceID,
		Payload: PystepsLKRequestedPayload{
			InputURI: input.InputURI, OutputPrefix: outputPrefix,
			IssueTime: issueTime, GridID: input.GridID,
			InputAssetIDs: input.InputAssetIDs,
			ModelID:       input.ModelID, ModelVersion: input.ModelVersion,
			ConfigVersion:           input.ConfigVersion,
			ForecastContractVersion: input.ForecastContractVersion,
			BaselineModels:          input.BaselineModels,
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("encode pySTEPS-LK request: %w", err)
	}
	run := workflow.Run{
		ID: input.RunID, IssueTime: issueTime, GridID: input.GridID,
		Status: workflow.RunBaselineRunning, UpdatedAt: now,
	}
	job := workflow.Job{
		ID: jobID, RunID: input.RunID, TraceID: traceID, JobType: PystepsLKJobType,
		ModelID: input.ModelID, ModelVersion: input.ModelVersion,
		ConfigVersion: input.ConfigVersion, Status: workflow.JobPending,
		Attempt: 1, RequestPayload: payload, CreatedAt: now,
	}
	bundle := workflow.PystepsLKBundle{
		Run: run, NowcastInputJob: input.NowcastInputJobID,
		InputURI: input.InputURI, InputAssetIDs: input.InputAssetIDs,
		ModelRunID: modelRunID, Config: input.Config, ConfigSHA256: input.ConfigSHA256,
		Job: job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(),
			EventType: PystepsLKRequestedEventType,
			Subject:   PystepsLKRequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreatePystepsLKBundle(ctx, bundle); err != nil {
		return workflow.Run{}, workflow.Job{}, err
	}
	return run, job, nil
}

func validatePystepsLKInput(input PystepsLKInput) error {
	parsed, err := url.ParseRequestURI(input.InputURI)
	if input.RunID == uuid.Nil || input.NowcastInputJobID == uuid.Nil ||
		input.IssueTime.IsZero() || input.GridID == "" || err != nil || parsed.Scheme != "s3" {
		return fmt.Errorf("pySTEPS-LK committed NowcastInput identity is required")
	}
	if input.CurrentStatus != workflow.RunInputReady {
		return fmt.Errorf("pySTEPS-LK requires forecast run state INPUT_READY")
	}
	if !input.IssueTime.UTC().Equal(input.IssueTime.UTC().Truncate(5 * time.Minute)) {
		return fmt.Errorf("pySTEPS-LK issue time must be on a five-minute UTC boundary")
	}
	if input.ModelID != PystepsLKModelID || input.ModelVersion != PystepsLKModelVersion ||
		input.ConfigVersion == "" || input.ForecastContractVersion != "1.1" {
		return fmt.Errorf("pySTEPS-LK model and contract identity differs from the active profile")
	}
	if len(input.BaselineModels) != 2 || input.BaselineModels[0] != "persistence" ||
		input.BaselineModels[1] != "translation" {
		return fmt.Errorf("pySTEPS-LK requires persistence and translation baselines")
	}
	if len(input.InputAssetIDs) == 0 {
		return fmt.Errorf("pySTEPS-LK input assets are required")
	}
	seen := make(map[uuid.UUID]struct{}, len(input.InputAssetIDs))
	for _, assetID := range input.InputAssetIDs {
		if assetID == uuid.Nil {
			return fmt.Errorf("pySTEPS-LK input asset ID cannot be nil")
		}
		if _, exists := seen[assetID]; exists {
			return fmt.Errorf("pySTEPS-LK input asset IDs must be unique")
		}
		seen[assetID] = struct{}{}
	}
	if len(input.Config) == 0 || !json.Valid(input.Config) ||
		!sha256Pattern.MatchString(input.ConfigSHA256) {
		return fmt.Errorf("pySTEPS-LK configuration and SHA-256 are required")
	}
	return nil
}

func (service *Service) CreateProductBuild(
	ctx context.Context,
	input ProductBuildInput,
) (workflow.Run, workflow.Job, error) {
	if err := validateProductBuildInput(input); err != nil {
		return workflow.Run{}, workflow.Job{}, err
	}
	now := service.now().UTC()
	jobID := stableID(
		"application-product-job",
		input.RunID.String(),
		input.ModelRunID.String(),
		input.ProductConfigVersion,
	)
	traceID := stableID("application-product-trace", jobID.String())
	eventID := stableID("application-product-request", jobID.String())
	productIDs := map[workflow.ProductType]uuid.UUID{
		workflow.ProductRainRate: stableID(
			"application-product", jobID.String(), string(workflow.ProductRainRate),
		),
		workflow.ProductAccumulation60: stableID(
			"application-product", jobID.String(), string(workflow.ProductAccumulation60),
		),
		workflow.ProductAccumulation120: stableID(
			"application-product", jobID.String(), string(workflow.ProductAccumulation120),
		),
	}
	outputPrefix := fmt.Sprintf(
		"s3://rainpulse/products/%s/%s/%s/distribution/%s/",
		input.RunID,
		url.PathEscape(input.ModelID),
		url.PathEscape(input.ModelVersion),
		url.PathEscape(input.ProductConfigVersion),
	)
	request := ProductBuildRequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID,
		EventType:     ProductBuildRequestedEventType,
		OccurredAt:    now,
		RunID:         input.RunID,
		JobID:         jobID,
		TraceID:       traceID,
		Payload: ProductBuildRequestedPayload{
			InputURI: input.ForecastURI, InputSHA256: input.ForecastSHA256,
			OutputPrefix: outputPrefix, ModelRunID: input.ModelRunID,
			IssueTime: input.IssueTime.UTC(), GridID: input.GridID,
			ModelID: input.ModelID, ModelVersion: input.ModelVersion,
			ModelConfigVersion:    input.ModelConfigVersion,
			ProductConfigVersion:  input.ProductConfigVersion,
			ProductBundleContract: input.ProductBundleContract,
			ProductIDs: ProductIDs{
				RainRate:        productIDs[workflow.ProductRainRate],
				Accumulation60:  productIDs[workflow.ProductAccumulation60],
				Accumulation120: productIDs[workflow.ProductAccumulation120],
			},
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("encode product-build request: %w", err)
	}
	run := workflow.Run{
		ID: input.RunID, IssueTime: input.IssueTime.UTC(), GridID: input.GridID,
		Status: workflow.RunProductBuilding, UpdatedAt: now,
	}
	job := workflow.Job{
		ID: jobID, RunID: input.RunID, TraceID: traceID, JobType: ProductBuildJobType,
		ModelID: input.ModelID, ModelVersion: input.ModelVersion,
		ConfigVersion: input.ProductConfigVersion, Status: workflow.JobPending,
		Attempt: 1, RequestPayload: payload, CreatedAt: now,
	}
	bundle := workflow.ProductBuildBundle{
		Run: run, ModelRunID: input.ModelRunID,
		ForecastURI: input.ForecastURI, ForecastSHA256: input.ForecastSHA256,
		InputAssetIDs: input.InputAssetIDs, ProductIDs: productIDs,
		ModelConfigVersion: input.ModelConfigVersion,
		ProductConfig:      input.ProductConfig, ProductConfigSHA256: input.ProductConfigSHA256,
		BundleContract: input.ProductBundleContract, Job: job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(),
			EventType: ProductBuildRequestedEventType,
			Subject:   ProductBuildRequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreateProductBuildBundle(ctx, bundle); err != nil {
		return workflow.Run{}, workflow.Job{}, err
	}
	return run, job, nil
}

func validateProductBuildInput(input ProductBuildInput) error {
	parsed, err := url.ParseRequestURI(input.ForecastURI)
	if input.RunID == uuid.Nil || input.ModelRunID == uuid.Nil || input.IssueTime.IsZero() ||
		input.GridID == "" || err != nil || parsed.Scheme != "s3" ||
		!sha256Pattern.MatchString(input.ForecastSHA256) {
		return fmt.Errorf("product build requires a committed ForecastOutput identity")
	}
	if input.CurrentStatus != workflow.RunBaselineReady {
		return fmt.Errorf("product build requires forecast run state BASELINE_READY")
	}
	if !input.IssueTime.UTC().Equal(input.IssueTime.UTC().Truncate(5 * time.Minute)) {
		return fmt.Errorf("product build issue time must be on a five-minute UTC boundary")
	}
	if input.ModelID != PystepsLKModelID || input.ModelVersion != PystepsLKModelVersion ||
		input.ModelConfigVersion == "" || input.ProductConfigVersion == "" ||
		input.ProductBundleContract != "1.0" {
		return fmt.Errorf("product build model or contract identity differs from RP-015")
	}
	if len(input.InputAssetIDs) == 0 || len(input.ProductConfig) == 0 ||
		!json.Valid(input.ProductConfig) || !sha256Pattern.MatchString(input.ProductConfigSHA256) {
		return fmt.Errorf("product build provenance and configuration are required")
	}
	return nil
}

func (service *Service) CreateForecastVerification(
	ctx context.Context,
	input ForecastVerificationInput,
) (workflow.Run, workflow.Job, error) {
	if err := validateForecastVerificationInput(input); err != nil {
		return workflow.Run{}, workflow.Job{}, err
	}
	now := service.now().UTC()
	jobID := stableID(
		"forecast-verification-job", input.RunID.String(),
		input.VerificationConfigVersion, input.ForecastSHA256,
	)
	traceID := stableID("forecast-verification-trace", jobID.String())
	eventID := stableID("forecast-verification-request", jobID.String())
	truthFrames := make([]ForecastVerificationRequestedTruth, len(input.Truth))
	for index, frame := range input.Truth {
		truthFrames[index] = ForecastVerificationRequestedTruth{
			AnalysisID: frame.AnalysisID, ValidTime: frame.ValidTime.UTC(),
			InputURI: frame.URI, InputSHA256: frame.SHA256,
		}
	}
	outputPrefix := fmt.Sprintf(
		"s3://rainpulse/verification/%s/%s/",
		input.RunID, url.PathEscape(input.VerificationConfigVersion),
	)
	request := ForecastVerificationRequested{
		SchemaVersion: SchemaVersion, EventID: eventID,
		EventType: ForecastVerificationRequestedEventType, OccurredAt: now,
		RunID: input.RunID, JobID: jobID, TraceID: traceID,
		Payload: ForecastVerificationRequestedPayload{
			ForecastURI: input.ForecastURI, ForecastSHA256: input.ForecastSHA256,
			TruthFrames: truthFrames, OutputPrefix: outputPrefix,
			IssueTime: input.IssueTime.UTC(), GridID: input.GridID,
			ModelID: input.ModelID, ModelVersion: input.ModelVersion,
			ForecastContractVersion:   input.ForecastContractVersion,
			VerificationConfigVersion: input.VerificationConfigVersion,
			ResultContractVersion:     input.ResultContractVersion,
		},
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("encode verification request: %w", err)
	}
	run := workflow.Run{
		ID: input.RunID, IssueTime: input.IssueTime.UTC(), GridID: input.GridID,
		Status: workflow.RunVerifying, UpdatedAt: now,
	}
	job := workflow.Job{
		ID: jobID, RunID: input.RunID, TraceID: traceID,
		JobType: ForecastVerificationJobType, ModelID: input.ModelID,
		ModelVersion: input.ModelVersion, ConfigVersion: input.VerificationConfigVersion,
		Status: workflow.JobPending, Attempt: 1, RequestPayload: payload, CreatedAt: now,
	}
	bundle := workflow.ForecastVerificationBundle{
		Run: run, ForecastURI: input.ForecastURI, ForecastSHA256: input.ForecastSHA256,
		Truth:                    append([]workflow.ForecastVerificationTruth(nil), input.Truth...),
		ForecastContractVersion:  input.ForecastContractVersion,
		ResultContractVersion:    input.ResultContractVersion,
		VerificationConfig:       input.VerificationConfig,
		VerificationConfigSHA256: input.VerificationConfigSHA256,
		Job:                      job,
		Outbox: workflow.OutboxEvent{
			ID: eventID, AggregateID: jobID.String(),
			EventType: ForecastVerificationRequestedEventType,
			Subject:   ForecastVerificationRequestedSubject, Payload: payload,
		},
	}
	if err := service.repository.CreateForecastVerificationBundle(ctx, bundle); err != nil {
		return workflow.Run{}, workflow.Job{}, err
	}
	return run, job, nil
}

func validateForecastVerificationInput(input ForecastVerificationInput) error {
	forecastURI, forecastErr := url.ParseRequestURI(input.ForecastURI)
	if input.RunID == uuid.Nil || input.IssueTime.IsZero() || input.GridID == "" ||
		forecastErr != nil || forecastURI.Scheme != "s3" ||
		!sha256Pattern.MatchString(input.ForecastSHA256) {
		return fmt.Errorf("verification requires a committed ForecastOutput identity")
	}
	if input.CurrentStatus != workflow.RunPublished {
		return fmt.Errorf("verification requires forecast run state PUBLISHED")
	}
	if !input.IssueTime.UTC().Equal(input.IssueTime.UTC().Truncate(5*time.Minute)) ||
		input.ModelID != PystepsLKModelID || input.ModelVersion != PystepsLKModelVersion ||
		input.ForecastContractVersion != "1.1" || input.ResultContractVersion != "1.0" {
		return fmt.Errorf("verification model, time, or contract identity differs from RP-031")
	}
	if len(input.Truth) != 24 {
		return fmt.Errorf("verification requires 24 five-minute truth frames")
	}
	seenIDs := make(map[uuid.UUID]struct{}, len(input.Truth))
	seenURIs := make(map[string]struct{}, len(input.Truth))
	for index, frame := range input.Truth {
		expectedTime := input.IssueTime.UTC().Add(time.Duration(index+1) * 5 * time.Minute)
		parsed, uriErr := url.ParseRequestURI(frame.URI)
		if frame.AnalysisID == uuid.Nil || !frame.ValidTime.UTC().Equal(expectedTime) ||
			uriErr != nil || parsed.Scheme != "s3" || !sha256Pattern.MatchString(frame.SHA256) {
			return fmt.Errorf("verification truth frame %d has invalid identity", index)
		}
		if _, exists := seenIDs[frame.AnalysisID]; exists {
			return fmt.Errorf("verification truth analysis IDs must be unique")
		}
		if _, exists := seenURIs[frame.URI]; exists {
			return fmt.Errorf("verification truth URIs must be unique")
		}
		seenIDs[frame.AnalysisID] = struct{}{}
		seenURIs[frame.URI] = struct{}{}
	}
	if input.VerificationConfigVersion == "" || len(input.VerificationConfig) == 0 ||
		!json.Valid(input.VerificationConfig) ||
		!sha256Pattern.MatchString(input.VerificationConfigSHA256) {
		return fmt.Errorf("verification configuration and SHA-256 are required")
	}
	return nil
}

func validateAndSelectNowcastFrames(
	input NowcastInputInput,
) ([]workflow.NowcastInputFrame, error) {
	issueTime := input.IssueTime.UTC()
	if input.IssueTime.IsZero() || input.GridID == "" || input.GridConfigVersion == "" ||
		input.PreprocessVersion == "" || input.GateConfigVersion == "" {
		return nil, fmt.Errorf("NowcastInput identity and version fields are required")
	}
	if input.ExecutionMode != "operational" && input.ExecutionMode != "historical_replay" {
		return nil, fmt.Errorf("NowcastInput execution mode is invalid")
	}
	if input.ExecutionMode == "operational" && !input.RequireAllFramesOperationalEligible {
		return nil, fmt.Errorf("operational NowcastInput requires eligible analysis frames")
	}
	if input.ExecutionMode == "historical_replay" && input.RequireAllFramesOperationalEligible {
		return nil, fmt.Errorf("historical replay must retain engineering analysis frames")
	}
	if input.MinimumFrames != 3 || input.MaximumFrames != 6 ||
		input.Timestep != 5*time.Minute {
		return nil, fmt.Errorf("Phase-1 NowcastInput requires 3-6 frames at five-minute steps")
	}
	if !issueTime.Equal(issueTime.Truncate(input.Timestep)) {
		return nil, fmt.Errorf("NowcastInput issue time must be on a five-minute UTC boundary")
	}
	if input.MinimumValidCoverageRatio < 0 || input.MinimumValidCoverageRatio > 1 ||
		input.MinimumMeanQualityIndex < 0 || input.MinimumMeanQualityIndex > 1 {
		return nil, fmt.Errorf("NowcastInput quality gates must be within [0, 1]")
	}
	if len(input.Config) == 0 || !json.Valid(input.Config) ||
		!sha256Pattern.MatchString(input.ConfigSHA256) {
		return nil, fmt.Errorf("NowcastInput configuration and SHA-256 are required")
	}
	byTime := make(map[time.Time]NowcastInputCandidate, len(input.Candidates))
	for _, candidate := range input.Candidates {
		candidateTime := candidate.AnalysisTime.UTC()
		parsed, uriErr := url.ParseRequestURI(candidate.AnalysisURI)
		if candidate.AnalysisID == uuid.Nil || candidate.AnalysisTime.IsZero() ||
			candidate.GridID != input.GridID || candidate.CurrentStatus != workflow.AnalysisReady ||
			uriErr != nil || parsed.Scheme != "s3" {
			continue
		}
		if _, exists := byTime[candidateTime]; exists {
			return nil, fmt.Errorf("multiple RadarAnalysis candidates share analysis time %s", candidateTime)
		}
		byTime[candidateTime] = candidate
	}
	selectedReverse := make([]workflow.NowcastInputFrame, 0, input.MaximumFrames)
	for offset := 0; offset < input.MaximumFrames; offset++ {
		expected := issueTime.Add(-time.Duration(offset) * input.Timestep)
		candidate, exists := byTime[expected]
		if !exists {
			break
		}
		if input.RequireAllFramesOperationalEligible && !candidate.OperationalEligible {
			return nil, fmt.Errorf("RadarAnalysis %s is not operationally eligible", candidate.AnalysisID)
		}
		if candidate.ValidCoverageRatio < input.MinimumValidCoverageRatio {
			return nil, fmt.Errorf("RadarAnalysis %s valid coverage is below gate", candidate.AnalysisID)
		}
		if candidate.MeanQualityIndex < input.MinimumMeanQualityIndex {
			return nil, fmt.Errorf("RadarAnalysis %s mean quality is below gate", candidate.AnalysisID)
		}
		selectedReverse = append(selectedReverse, workflow.NowcastInputFrame{
			AnalysisID: candidate.AnalysisID, AnalysisTime: expected,
			InputURI:           candidate.AnalysisURI,
			ValidCoverageRatio: candidate.ValidCoverageRatio,
			MeanQualityIndex:   candidate.MeanQualityIndex,
		})
	}
	if len(selectedReverse) < input.MinimumFrames {
		return nil, fmt.Errorf(
			"NowcastInput requires at least %d contiguous frames ending at %s; found %d",
			input.MinimumFrames, issueTime.Format(time.RFC3339), len(selectedReverse),
		)
	}
	frames := make([]workflow.NowcastInputFrame, len(selectedReverse))
	for index := range selectedReverse {
		frames[len(selectedReverse)-1-index] = selectedReverse[index]
	}
	return frames, nil
}

func alignMosaicCandidates(
	input AnalysisMosaicInput,
	analysisTime time.Time,
) ([]AnalysisMosaicCandidate, []workflow.AnalysisRadar) {
	candidates := append([]AnalysisMosaicCandidate(nil), input.Candidates...)
	sort.Slice(candidates, func(left, right int) bool {
		if candidates[left].RadarID != candidates[right].RadarID {
			return candidates[left].RadarID < candidates[right].RadarID
		}
		leftOffset := math.Abs(candidates[left].VolumeEndTime.Sub(analysisTime).Seconds())
		rightOffset := math.Abs(candidates[right].VolumeEndTime.Sub(analysisTime).Seconds())
		if leftOffset != rightOffset {
			return leftOffset < rightOffset
		}
		return candidates[left].ScanID.String() < candidates[right].ScanID.String()
	})
	byRadar := make(map[string][]AnalysisMosaicCandidate)
	for _, candidate := range candidates {
		byRadar[candidate.RadarID] = append(byRadar[candidate.RadarID], candidate)
	}
	radarSet := make(map[string]struct{})
	for radarID := range byRadar {
		radarSet[radarID] = struct{}{}
	}
	for _, radarID := range input.ExpectedRadarIDs {
		radarSet[radarID] = struct{}{}
	}
	radarIDs := make([]string, 0, len(radarSet))
	for radarID := range radarSet {
		radarIDs = append(radarIDs, radarID)
	}
	sort.Strings(radarIDs)
	selected := make([]AnalysisMosaicCandidate, 0, len(radarIDs))
	radars := make([]workflow.AnalysisRadar, 0, len(radarIDs))
	for _, radarID := range radarIDs {
		var chosen *AnalysisMosaicCandidate
		for index := range byRadar[radarID] {
			candidate := &byRadar[radarID][index]
			if candidate.CurrentStatus != workflow.RadarScanGridReady {
				continue
			}
			if absDuration(candidate.VolumeEndTime.UTC().Sub(analysisTime)) >
				input.MaximumAbsoluteOffset {
				continue
			}
			chosen = candidate
			break
		}
		if chosen != nil {
			offset := int(math.Round(chosen.VolumeEndTime.UTC().Sub(analysisTime).Seconds()))
			scanID := chosen.ScanID
			selected = append(selected, *chosen)
			radars = append(radars, workflow.AnalysisRadar{
				RadarID: radarID, ScanID: &scanID,
				State:             workflow.AnalysisRadarParticipating,
				TimeOffsetSeconds: &offset,
			})
			continue
		}
		reason := "no_ready_grid_within_alignment_window"
		state := workflow.AnalysisRadarExcluded
		if len(byRadar[radarID]) == 0 {
			reason = "expected_radar_missing"
			state = workflow.AnalysisRadarMissing
		}
		radars = append(radars, workflow.AnalysisRadar{
			RadarID: radarID, State: state, ExclusionReason: &reason,
		})
	}
	return selected, radars
}

func absDuration(value time.Duration) time.Duration {
	if value < 0 {
		return -value
	}
	return value
}

func stableID(parts ...string) uuid.UUID {
	return uuid.NewSHA1(uuid.NameSpaceURL, []byte("rainpulse:"+strings.Join(parts, ":")))
}

func (service *Service) CreateSimulation(ctx context.Context, issueTime time.Time) (workflow.Run, workflow.Job, error) {
	return service.createSimulation(ctx, issueTime, nil, "forecast workflow simulation")
}

func (service *Service) CreateThreeWorkflowSimulation(
	ctx context.Context,
	analysisTime time.Time,
) (workflow.DomainSimulation, error) {
	now := service.now().UTC()
	analysisTime = analysisTime.UTC()
	displayA := "Synthetic radar A"
	displayB := "Synthetic radar B"
	reason := "synthetic radar B failed; analysis continued with radar A"
	failedReason := "SIMULATED_RADAR_FAILURE"
	normalizedURI := "s3://rainpulse/simulations/radar-a/normalized/volume.zarr"
	qcURI := "s3://rainpulse/simulations/radar-a/qc/volume.zarr"
	gridURI := "s3://rainpulse/simulations/radar-a/grid/grid.zarr"
	analysisURI := "s3://rainpulse/simulations/analysis/analysis.zarr"
	complete := 1.0
	quality := 0.82
	failedCompleteness := 0.5
	zeroOffset := 0

	scanAID := service.newID()
	scanARunID := service.newID()
	scanBID := service.newID()
	scanBRunID := service.newID()
	analysisID := service.newID()
	analysisRunID := service.newID()
	simulation := workflow.DomainSimulation{
		Radars: []workflow.Radar{
			{
				ID: "synthetic_radar_a", DisplayName: &displayA,
				Lifecycle: workflow.RadarReady, ConfigVersion: "rp004-synthetic-radar-a-v1",
				CreatedAt: now, UpdatedAt: now,
			},
			{
				ID: "synthetic_radar_b", DisplayName: &displayB,
				Lifecycle: workflow.RadarReady, ConfigVersion: "rp004-synthetic-radar-b-v1",
				CreatedAt: now, UpdatedAt: now,
			},
		},
		Scans: []workflow.RadarScan{
			{
				ID: scanAID, RunID: scanARunID, RadarID: "synthetic_radar_a",
				VolumeStartTime: analysisTime.Add(-10 * time.Second),
				VolumeEndTime:   analysisTime.Add(-2 * time.Second), ReceivedAt: analysisTime,
				RadarConfigVersion: "rp004-synthetic-radar-a-v1",
				Status:             workflow.RadarScanGridReady, NormalizedURI: &normalizedURI,
				QCURI: &qcURI, GridURI: &gridURI, ScanCompleteness: &complete,
				MeanQualityIndex: &quality, CreatedAt: now, UpdatedAt: now,
			},
			{
				ID: scanBID, RunID: scanBRunID, RadarID: "synthetic_radar_b",
				VolumeStartTime: analysisTime.Add(-12 * time.Second),
				VolumeEndTime:   analysisTime.Add(-4 * time.Second), ReceivedAt: analysisTime,
				RadarConfigVersion: "rp004-synthetic-radar-b-v1",
				Status:             workflow.RadarScanFailed, DegradedReason: &failedReason,
				ScanCompleteness: &failedCompleteness, CreatedAt: now, UpdatedAt: now,
			},
		},
		Analysis: workflow.AnalysisCycle{
			ID: analysisID, RunID: analysisRunID, AnalysisTime: analysisTime,
			GridID: "rp004-synthetic-grid", ConfigVersion: "rp004-synthetic-analysis-v1",
			Status: workflow.AnalysisReady, DegradedReason: &reason, RadarCount: 1,
			ValidCoverageRatio: float64Pointer(0.86), MeanQualityIndex: float64Pointer(0.82),
			AnalysisURI: &analysisURI,
			Radars: []workflow.AnalysisRadar{
				{
					RadarID: "synthetic_radar_a", ScanID: &scanAID,
					State:             workflow.AnalysisRadarParticipating,
					TimeOffsetSeconds: &zeroOffset, MeanQualityIndex: &quality,
				},
				{
					RadarID: "synthetic_radar_b", ScanID: &scanBID,
					State: workflow.AnalysisRadarFailed, ExclusionReason: &failedReason,
				},
			},
			CreatedAt: now, UpdatedAt: now,
		},
	}
	if err := service.repository.CreateDomainSimulation(ctx, simulation); err != nil {
		return workflow.DomainSimulation{}, err
	}
	return simulation, nil
}

func (service *Service) Rerun(ctx context.Context, sourceRunID uuid.UUID) (workflow.Run, error) {
	source, err := service.repository.GetRun(ctx, sourceRunID)
	if err != nil {
		return workflow.Run{}, err
	}
	jobs, err := service.repository.ListJobs(ctx, sourceRunID)
	if err != nil {
		return workflow.Run{}, err
	}
	if len(jobs) == 0 {
		return workflow.Run{}, fmt.Errorf("source run has no jobs")
	}
	if source.GridID != SimulationGrid || source.ConfigVersion != SimulationConfig ||
		jobs[0].ModelID != SimulationModelID {
		return workflow.Run{}, ErrUnsupportedRerun
	}

	requested, err := decodeJobRequested(jobs[0].RequestPayload)
	if err != nil {
		return workflow.Run{}, fmt.Errorf("decode source job request: %w", err)
	}
	run, _, err := service.create(ctx, createSpec{
		IssueTime:     source.IssueTime,
		GridID:        source.GridID,
		ConfigVersion: source.ConfigVersion,
		ModelID:       jobs[0].ModelID,
		ModelVersion:  jobs[0].ModelVersion,
		JobType:       jobs[0].JobType,
		InputURI:      requested.Payload.InputURI,
		InputAssets:   requested.Payload.InputAssets,
		Parameters:    requested.Payload.Parameters,
		RerunOf:       &sourceRunID,
		Reason:        "manual rerun",
	})
	return run, err
}

func (service *Service) DispatchOnce(ctx context.Context, publisher Publisher) (bool, error) {
	event, err := service.repository.ClaimOutbox(ctx)
	if errors.Is(err, workflow.ErrNotFound) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if err := publisher.Publish(ctx, event); err != nil {
		_ = service.repository.MarkOutboxFailed(ctx, event.ID, err.Error())
		return true, fmt.Errorf("publish outbox event: %w", err)
	}
	if err := service.repository.MarkOutboxPublished(ctx, event.ID); err != nil {
		return true, fmt.Errorf("mark outbox event published: %w", err)
	}
	return true, nil
}

func (service *Service) HandleCompletion(ctx context.Context, data []byte) (bool, error) {
	event, err := DecodeJobCompleted(data)
	if err != nil {
		return false, err
	}
	return service.repository.ApplyCompletion(ctx, event, json.RawMessage(data))
}

func (service *Service) HandleResult(ctx context.Context, data []byte) (bool, error) {
	var envelope struct {
		EventType string `json:"event_type"`
	}
	if err := json.Unmarshal(data, &envelope); err != nil {
		return false, fmt.Errorf("%w: decode result envelope: %v", ErrInvalidEvent, err)
	}
	switch envelope.EventType {
	case JobCompletedEventType:
		return service.HandleCompletion(ctx, data)
	case JobFailedEventType:
		event, err := DecodeJobFailed(data)
		if err != nil {
			return false, err
		}
		return service.repository.ApplyFailure(ctx, event, json.RawMessage(data))
	default:
		return false, fmt.Errorf("%w: unsupported result event type %q", ErrInvalidEvent, envelope.EventType)
	}
}

func (service *Service) CreateFailureSimulation(ctx context.Context, issueTime time.Time) (workflow.Run, workflow.Job, error) {
	return service.create(ctx, createSpec{
		IssueTime:     issueTime,
		GridID:        SimulationGrid,
		ConfigVersion: SimulationConfig,
		ModelID:       SimulationModelID,
		ModelVersion:  SimulationModelVersion,
		JobType:       SimulationJobType,
		InputURI:      "s3://rainpulse/simulations/input.zarr",
		InputAssets:   []uuid.UUID{},
		Parameters:    map[string]any{"simulation": true, "force_failure": true},
		Reason:        "RP-005 simulated worker failure",
	})
}

func (service *Service) BuildSimulationCompletion(job workflow.Job) JobCompleted {
	finishedAt := service.now().UTC()
	startedAt := finishedAt.Add(-500 * time.Millisecond)
	return JobCompleted{
		SchemaVersion: SchemaVersion,
		EventID:       service.newID(),
		EventType:     JobCompletedEventType,
		OccurredAt:    finishedAt,
		RunID:         job.RunID,
		JobID:         job.ID,
		TraceID:       job.TraceID,
		Payload: JobCompletedPayload{
			Status:     "succeeded",
			StartedAt:  startedAt,
			FinishedAt: finishedAt,
			RuntimeMS:  500,
			Assets: []JobCompletedAsset{{
				AssetType: "forecast_zarr",
				URI:       fmt.Sprintf("s3://rainpulse/simulations/%s/forecast.zarr", job.RunID),
				SHA256:    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
				SizeBytes: 3,
				MediaType: "application/vnd+zarr",
			}},
			Metrics: map[string]float64{"simulation": 1},
		},
	}
}

type createSpec struct {
	IssueTime     time.Time
	GridID        string
	ConfigVersion string
	ModelID       string
	ModelVersion  string
	JobType       string
	InputURI      string
	InputAssets   []uuid.UUID
	Parameters    map[string]any
	RerunOf       *uuid.UUID
	Reason        string
}

func (service *Service) createSimulation(ctx context.Context, issueTime time.Time, rerunOf *uuid.UUID, reason string) (workflow.Run, workflow.Job, error) {
	return service.create(ctx, createSpec{
		IssueTime:     issueTime,
		GridID:        SimulationGrid,
		ConfigVersion: SimulationConfig,
		ModelID:       SimulationModelID,
		ModelVersion:  SimulationModelVersion,
		JobType:       SimulationJobType,
		InputURI:      "s3://rainpulse/simulations/input.zarr",
		InputAssets:   []uuid.UUID{},
		Parameters:    map[string]any{"simulation": true},
		RerunOf:       rerunOf,
		Reason:        reason,
	})
}

func (service *Service) create(ctx context.Context, spec createSpec) (workflow.Run, workflow.Job, error) {
	now := service.now().UTC()
	runID := service.newID()
	jobID := service.newID()
	traceID := service.newID()
	eventID := service.newID()

	requested := JobRequested{
		SchemaVersion: SchemaVersion,
		EventID:       eventID,
		EventType:     JobRequestedEventType,
		OccurredAt:    now,
		RunID:         runID,
		JobID:         jobID,
		TraceID:       traceID,
		Payload: JobRequestedPayload{
			JobType:      spec.JobType,
			InputURI:     spec.InputURI,
			OutputPrefix: fmt.Sprintf("s3://rainpulse/simulations/%s/", runID),
			GridID:       spec.GridID,
			Config:       spec.ConfigVersion,
			Model:        spec.ModelVersion,
			IssueTime:    spec.IssueTime.UTC(),
			InputAssets:  spec.InputAssets,
			Parameters:   spec.Parameters,
		},
	}
	payload, err := json.Marshal(requested)
	if err != nil {
		return workflow.Run{}, workflow.Job{}, fmt.Errorf("encode job.requested: %w", err)
	}

	run := workflow.Run{
		ID:            runID,
		IssueTime:     spec.IssueTime.UTC(),
		GridID:        spec.GridID,
		ConfigVersion: spec.ConfigVersion,
		Status:        workflow.RunBaselineRunning,
		RerunOf:       spec.RerunOf,
		CreatedAt:     now,
		UpdatedAt:     now,
	}
	job := workflow.Job{
		ID:             jobID,
		RunID:          runID,
		TraceID:        traceID,
		JobType:        spec.JobType,
		ModelID:        spec.ModelID,
		ModelVersion:   spec.ModelVersion,
		ConfigVersion:  spec.ConfigVersion,
		Status:         workflow.JobPending,
		Attempt:        1,
		RequestPayload: payload,
		CreatedAt:      now,
	}
	bundle := workflow.CreateBundle{
		Run: run,
		Job: job,
		Outbox: workflow.OutboxEvent{
			ID:          eventID,
			AggregateID: jobID.String(),
			EventType:   JobRequestedEventType,
			Subject:     JobRequestedSubject,
			Payload:     payload,
		},
	}
	if err := service.repository.CreateBundle(ctx, bundle); err != nil {
		return workflow.Run{}, workflow.Job{}, err
	}
	return run, job, nil
}

func decodeJobRequested(data []byte) (JobRequested, error) {
	var event JobRequested
	if err := json.Unmarshal(data, &event); err != nil {
		return JobRequested{}, err
	}
	return event, nil
}

func float64Pointer(value float64) *float64 {
	return &value
}

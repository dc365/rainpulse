package orchestration

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

type Repository interface {
	CreateBundle(context.Context, workflow.CreateBundle) error
	CreateRadarDecodeBundle(context.Context, workflow.RadarDecodeBundle) error
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
			InputURI: input.InputURI, OutputPrefix: outputPrefix,
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
	if err != nil || parsed.Scheme != "file" {
		return fmt.Errorf("radar decoder input must be a file URI")
	}
	if input.InputSizeBytes < 0 || input.VolumeStartTime.IsZero() || input.VolumeEndTime.Before(input.VolumeStartTime) {
		return fmt.Errorf("invalid radar input size or volume time range")
	}
	return nil
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

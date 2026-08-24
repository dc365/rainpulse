package orchestration

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

type Repository interface {
	CreateBundle(context.Context, workflow.CreateBundle) error
	GetRun(context.Context, uuid.UUID) (workflow.Run, error)
	GetJob(context.Context, uuid.UUID) (workflow.Job, error)
	ListJobs(context.Context, uuid.UUID) ([]workflow.Job, error)
	ClaimOutbox(context.Context) (workflow.OutboxEvent, error)
	MarkOutboxPublished(context.Context, uuid.UUID) error
	MarkOutboxFailed(context.Context, uuid.UUID, string) error
	ApplyCompletion(context.Context, JobCompleted, json.RawMessage) (bool, error)
	ApplyFailure(context.Context, JobFailed, json.RawMessage) (bool, error)
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

func (service *Service) CreateSimulation(ctx context.Context, issueTime time.Time) (workflow.Run, workflow.Job, error) {
	return service.createSimulation(ctx, issueTime, nil, "RP-003 simulated run")
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
		Reason:        "RP-004 simulated worker failure",
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

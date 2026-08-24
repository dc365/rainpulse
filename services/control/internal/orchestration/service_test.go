package orchestration

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

func TestCreateSimulationPersistsContractEventInOneBundle(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 24, 3, 0, 1, 0, time.UTC)
	ids := []uuid.UUID{
		uuid.MustParse("f3641335-13a3-4f68-96c0-56a5e0e684d7"),
		uuid.MustParse("0894481f-c096-49af-8d32-e9c531a66772"),
		uuid.MustParse("0d049a59-754c-4405-8a31-d789685056c2"),
		uuid.MustParse("d3407132-8f23-49d1-8107-39b45c943760"),
	}
	service := NewService(repository, Options{
		Now: func() time.Time { return now },
		NewID: func() uuid.UUID {
			id := ids[0]
			ids = ids[1:]
			return id
		},
	})

	run, job, err := service.CreateSimulation(context.Background(), now.Add(-time.Second))
	if err != nil {
		t.Fatalf("CreateSimulation() error = %v", err)
	}
	if run.Status != workflow.RunBaselineRunning || job.Status != workflow.JobPending {
		t.Fatalf("unexpected initial states: run=%s job=%s", run.Status, job.Status)
	}
	if repository.created.Run.ID != run.ID || repository.created.Job.ID != job.ID {
		t.Fatal("run and job were not persisted in the bundle")
	}
	if repository.created.Outbox.Subject != JobRequestedSubject {
		t.Fatalf("unexpected task subject %q", repository.created.Outbox.Subject)
	}

	var requested JobRequested
	if err := json.Unmarshal(repository.created.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode requested event: %v", err)
	}
	if requested.SchemaVersion != SchemaVersion || requested.EventType != JobRequestedEventType {
		t.Fatalf("unexpected event contract: %#v", requested)
	}
	if requested.RunID != run.ID || requested.JobID != job.ID || requested.TraceID != job.TraceID {
		t.Fatal("event trace identifiers do not match persisted records")
	}
	if requested.Payload.JobType != SimulationJobType || requested.Payload.Model != SimulationModelVersion {
		t.Fatalf("unexpected simulation payload: %#v", requested.Payload)
	}
}

func TestDispatchOnceMarksPublishedOnlyAfterPublish(t *testing.T) {
	event := workflow.OutboxEvent{ID: uuid.New(), Subject: JobRequestedSubject, Payload: json.RawMessage(`{}`)}
	repository := &fakeRepository{claimed: event}
	publisher := &fakePublisher{}
	service := NewService(repository, Options{})

	dispatched, err := service.DispatchOnce(context.Background(), publisher)
	if err != nil || !dispatched {
		t.Fatalf("DispatchOnce() = %t, %v", dispatched, err)
	}
	if publisher.published.ID != event.ID || repository.published != event.ID {
		t.Fatal("event was not published and marked atomically in order")
	}

	repository = &fakeRepository{claimed: event}
	publisher.err = errors.New("NATS unavailable")
	service = NewService(repository, Options{})
	if _, err := service.DispatchOnce(context.Background(), publisher); err == nil {
		t.Fatal("expected publish error")
	}
	if repository.published != uuid.Nil || repository.failed != event.ID {
		t.Fatal("failed publish was incorrectly marked published")
	}
}

func TestDecodeJobCompletedRejectsUnknownFields(t *testing.T) {
	data := []byte(`{
      "schema_version":"1.0",
      "event_id":"241df97b-4d3c-43c6-8d25-a1dc8d3015e8",
      "event_type":"job.completed",
      "occurred_at":"2026-08-24T03:00:42Z",
      "run_id":"f3641335-13a3-4f68-96c0-56a5e0e684d7",
      "job_id":"0894481f-c096-49af-8d32-e9c531a66772",
      "trace_id":"0d049a59-754c-4405-8a31-d789685056c2",
      "unexpected":true,
      "payload":{"status":"succeeded","started_at":"2026-08-24T03:00:03Z","finished_at":"2026-08-24T03:00:42Z","runtime_ms":39000,"assets":[],"metrics":{}}
    }`)
	if _, err := DecodeJobCompleted(data); err == nil {
		t.Fatal("expected unknown field to be rejected")
	}
}

func TestHandleResultDispatchesStrictFailureEvent(t *testing.T) {
	repository := &fakeRepository{}
	service := NewService(repository, Options{})
	data := []byte(`{
      "schema_version":"1.0",
      "event_id":"e46ab270-f28a-5cf8-b49a-fcbeb77b8960",
      "event_type":"job.failed",
      "occurred_at":"2026-08-24T03:00:06Z",
      "run_id":"f3641335-13a3-4f68-96c0-56a5e0e684d7",
      "job_id":"0894481f-c096-49af-8d32-e9c531a66772",
      "trace_id":"0d049a59-754c-4405-8a31-d789685056c2",
      "payload":{"status":"failed","started_at":"2026-08-24T03:00:03Z","finished_at":"2026-08-24T03:00:06Z","runtime_ms":3000,"error_code":"SIMULATED_FAILURE","error_message":"RP-004 simulated worker failure","retryable":false,"details":{}}
    }`)

	applied, err := service.HandleResult(context.Background(), data)
	if err != nil || !applied || repository.appliedFailure.EventType != JobFailedEventType {
		t.Fatalf("HandleResult() = %t, %v, event=%#v", applied, err, repository.appliedFailure)
	}

	data = append(data[:len(data)-1], []byte(`,"unexpected":true}`)...)
	if _, err := service.HandleResult(context.Background(), data); err == nil {
		t.Fatal("expected unknown failure field to be rejected")
	}
}

type fakeRepository struct {
	created        workflow.CreateBundle
	claimed        workflow.OutboxEvent
	published      uuid.UUID
	failed         uuid.UUID
	appliedFailure JobFailed
}

func (repository *fakeRepository) CreateBundle(_ context.Context, bundle workflow.CreateBundle) error {
	repository.created = bundle
	return nil
}

func (repository *fakeRepository) GetRun(context.Context, uuid.UUID) (workflow.Run, error) {
	return workflow.Run{}, workflow.ErrNotFound
}

func (repository *fakeRepository) GetJob(context.Context, uuid.UUID) (workflow.Job, error) {
	return workflow.Job{}, workflow.ErrNotFound
}

func (repository *fakeRepository) ListJobs(context.Context, uuid.UUID) ([]workflow.Job, error) {
	return nil, nil
}

func (repository *fakeRepository) ClaimOutbox(context.Context) (workflow.OutboxEvent, error) {
	if repository.claimed.ID == uuid.Nil {
		return workflow.OutboxEvent{}, workflow.ErrNotFound
	}
	return repository.claimed, nil
}

func (repository *fakeRepository) MarkOutboxPublished(_ context.Context, eventID uuid.UUID) error {
	repository.published = eventID
	return nil
}

func (repository *fakeRepository) MarkOutboxFailed(_ context.Context, eventID uuid.UUID, _ string) error {
	repository.failed = eventID
	return nil
}

func (repository *fakeRepository) ApplyCompletion(context.Context, JobCompleted, json.RawMessage) (bool, error) {
	return true, nil
}

func (repository *fakeRepository) ApplyFailure(_ context.Context, event JobFailed, _ json.RawMessage) (bool, error) {
	repository.appliedFailure = event
	return true, nil
}

type fakePublisher struct {
	published workflow.OutboxEvent
	err       error
}

func (publisher *fakePublisher) Publish(_ context.Context, event workflow.OutboxEvent) error {
	publisher.published = event
	return publisher.err
}

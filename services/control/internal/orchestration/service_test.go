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

func TestCreateRadarDecodeUsesStableIdentityAndRealWorkerSubject(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 24, 12, 0, 0, 0, time.UTC)
	service := NewService(repository, Options{Now: func() time.Time { return now }})
	displayName := "SanMing Z9598"
	input := RadarDecodeInput{
		RadarID: "z9598", DisplayName: &displayName, Lifecycle: workflow.RadarDraft,
		ConfigVersion: "z9598-fmt-v1", Config: json.RawMessage(`{"radar_id":"z9598"}`),
		ConfigSHA256: "63266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f678",
		SourceFormat: "cma-rstm-level2", InputURI: "file:///data/Weather/sample.bin.bz2",
		InputSHA256:     "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
		InputSizeBytes:  3,
		VolumeStartTime: time.Date(2026, 6, 15, 11, 58, 29, 0, time.UTC),
		VolumeEndTime:   time.Date(2026, 6, 15, 12, 3, 52, 0, time.UTC),
	}

	scan, job, err := service.CreateRadarDecode(context.Background(), input)
	if err != nil {
		t.Fatalf("CreateRadarDecode() error = %v", err)
	}
	first := repository.radarDecode
	if first.Outbox.Subject != RadarDecodeRequestedSubject || first.Outbox.EventType != RadarDecodeRequestedEventType {
		t.Fatalf("unexpected radar worker route: %#v", first.Outbox)
	}
	if scan.Status != workflow.RadarScanRawValidating || job.JobType != RadarDecodeJobType {
		t.Fatalf("unexpected radar workflow states: scan=%s job=%s", scan.Status, job.JobType)
	}
	var requested RadarDecodeRequested
	if err := json.Unmarshal(first.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode radar request: %v", err)
	}
	if requested.Payload.ScanID != scan.ID || requested.Payload.DecoderVersion != RadarDecoderVersion ||
		requested.Payload.InputURI != input.InputURI {
		t.Fatalf("unexpected radar decode request: %#v", requested)
	}

	secondScan, secondJob, err := service.CreateRadarDecode(context.Background(), input)
	if err != nil {
		t.Fatalf("repeat CreateRadarDecode() error = %v", err)
	}
	if secondScan.ID != scan.ID || secondScan.RunID != scan.RunID || secondJob.ID != job.ID ||
		repository.radarDecode.Outbox.ID != first.Outbox.ID {
		t.Fatal("radar decode workflow identifiers are not deterministic")
	}
}

func TestCreateRadarQCUsesNormalizedInputAndStableIdentity(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 24, 13, 0, 0, 0, time.UTC)
	service := NewService(repository, Options{Now: func() time.Time { return now }})
	input := RadarQCInput{
		ScanID:  uuid.MustParse("10000000-0000-4000-8000-000000000004"),
		RunID:   uuid.MustParse("10000000-0000-4000-8000-000000000002"),
		RadarID: "z9598", RadarConfigVersion: "z9598-fmt-v1",
		NormalizedURI: "s3://rainpulse/radar/normalized/z9598/scan/volume.zarr",
		CurrentStatus: workflow.RadarScanNormalized,
		Health:        workflow.RadarHealthDegraded,
		QCProfile:     "rp008-basic-v1", QCPipelineVersion: "rp008-basic-1.0.0",
		FlagDefinitionVersion: "qc-flags-v1",
	}

	job, err := service.CreateRadarQC(context.Background(), input)
	if err != nil {
		t.Fatalf("CreateRadarQC() error = %v", err)
	}
	first := repository.radarQC
	if first.Outbox.Subject != RadarQCRequestedSubject || first.Outbox.EventType != RadarQCRequestedEventType {
		t.Fatalf("unexpected radar QC worker route: %#v", first.Outbox)
	}
	if job.JobType != RadarQCJobType || job.RunID != input.RunID {
		t.Fatalf("unexpected radar QC job: %#v", job)
	}
	var requested RadarQCRequested
	if err := json.Unmarshal(first.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode radar QC request: %v", err)
	}
	if requested.Payload.ScanID != input.ScanID || requested.Payload.InputURI != input.NormalizedURI ||
		requested.Payload.QCProfile != input.QCProfile {
		t.Fatalf("unexpected radar QC request: %#v", requested)
	}
	second, err := service.CreateRadarQC(context.Background(), input)
	if err != nil {
		t.Fatalf("repeat CreateRadarQC() error = %v", err)
	}
	if second.ID != job.ID || repository.radarQC.Outbox.ID != first.Outbox.ID {
		t.Fatal("radar QC workflow identifiers are not deterministic")
	}

	input.Health = workflow.RadarHealthUnavailable
	if _, err := service.CreateRadarQC(context.Background(), input); err == nil {
		t.Fatal("unavailable radar health must not enter QC")
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

func TestCreateThreeWorkflowSimulationKeepsAnalysisReadyAfterOneRadarFails(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 24, 3, 0, 1, 0, time.UTC)
	ids := []uuid.UUID{
		uuid.MustParse("10000000-0000-4000-8000-000000000001"),
		uuid.MustParse("10000000-0000-4000-8000-000000000002"),
		uuid.MustParse("10000000-0000-4000-8000-000000000003"),
		uuid.MustParse("10000000-0000-4000-8000-000000000004"),
		uuid.MustParse("10000000-0000-4000-8000-000000000005"),
		uuid.MustParse("10000000-0000-4000-8000-000000000006"),
	}
	service := NewService(repository, Options{
		Now: func() time.Time { return now },
		NewID: func() uuid.UUID {
			id := ids[0]
			ids = ids[1:]
			return id
		},
	})

	simulation, err := service.CreateThreeWorkflowSimulation(context.Background(), now)
	if err != nil {
		t.Fatalf("CreateThreeWorkflowSimulation() error = %v", err)
	}
	if len(simulation.Scans) != 2 || simulation.Scans[0].Status != workflow.RadarScanGridReady ||
		simulation.Scans[1].Status != workflow.RadarScanFailed {
		t.Fatalf("unexpected radar simulations: %#v", simulation.Scans)
	}
	if simulation.Analysis.Status != workflow.AnalysisReady ||
		simulation.Analysis.DegradedReason == nil || simulation.Analysis.RadarCount != 1 {
		t.Fatalf("analysis did not preserve degraded-ready semantics: %#v", simulation.Analysis)
	}
	if repository.domain.Analysis.ID != simulation.Analysis.ID {
		t.Fatal("domain simulation was not persisted as one repository operation")
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
      "payload":{"status":"failed","started_at":"2026-08-24T03:00:03Z","finished_at":"2026-08-24T03:00:06Z","runtime_ms":3000,"error_code":"SIMULATED_FAILURE","error_message":"RP-005 simulated worker failure","retryable":false,"details":{}}
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
	radarDecode    workflow.RadarDecodeBundle
	radarQC        workflow.RadarQCBundle
	domain         workflow.DomainSimulation
	claimed        workflow.OutboxEvent
	published      uuid.UUID
	failed         uuid.UUID
	appliedFailure JobFailed
}

func (repository *fakeRepository) CreateRadarQCBundle(
	_ context.Context,
	bundle workflow.RadarQCBundle,
) error {
	repository.radarQC = bundle
	return nil
}

func (repository *fakeRepository) CreateRadarDecodeBundle(
	_ context.Context,
	bundle workflow.RadarDecodeBundle,
) error {
	repository.radarDecode = bundle
	return nil
}

func (repository *fakeRepository) CreateDomainSimulation(
	_ context.Context,
	simulation workflow.DomainSimulation,
) error {
	repository.domain = simulation
	return nil
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

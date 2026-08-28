package orchestration

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
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
		SourceFormat: "cma-rstm-level2", InputURI: "s3://rainpulse/radar/raw/z9598/2026/08/24/030000Z/hash/sample.bin.bz2",
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
		requested.Payload.InputURI != input.InputURI ||
		requested.Payload.InputSHA256 != input.InputSHA256 ||
		requested.Payload.InputSizeBytes != input.InputSizeBytes {
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
		QCProfile:     "rp008-basic-v1", QCPipelineVersion: "rp008-basic-1.0.4",
		FlagDefinitionVersion: "qc-flags-v1",
		QCConfig:              json.RawMessage(`{"pipeline_version":"rp008-basic-1.0.4"}`),
		QCConfigSHA256:        "63266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f678",
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
	if string(first.Config) != string(input.QCConfig) || first.ConfigSHA256 != input.QCConfigSHA256 {
		t.Fatalf("radar QC configuration was not registered in the workflow bundle: %#v", first)
	}
	var requested RadarQCRequested
	if err := json.Unmarshal(first.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode radar QC request: %v", err)
	}
	if requested.Payload.ScanID != input.ScanID || requested.Payload.InputURI != input.NormalizedURI ||
		requested.Payload.QCProfile != input.QCProfile {
		t.Fatalf("unexpected radar QC request: %#v", requested)
	}
	if requested.Payload.OutputPrefix != "s3://rainpulse/radar/qc/z9598/"+input.ScanID.String()+"/rp008-basic-1.0.4/" {
		t.Fatalf("radar QC output must be isolated by pipeline version: %q", requested.Payload.OutputPrefix)
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

	input.Health = workflow.RadarHealthDegraded
	input.CurrentStatus = workflow.RadarScanFailed
	if _, err := service.CreateRadarQC(context.Background(), input); err != nil {
		t.Fatalf("failed QC scan with normalized input must be retryable: %v", err)
	}
}

func TestCreateRadarGridUsesQCInputAndVersionIsolatedOutput(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 25, 6, 0, 0, 0, time.UTC)
	service := NewService(repository, Options{Now: func() time.Time { return now }})
	input := RadarGridInput{
		ScanID:             uuid.MustParse("10000000-0000-4000-8000-000000000004"),
		RunID:              uuid.MustParse("10000000-0000-4000-8000-000000000002"),
		RadarID:            "z9598",
		QCURI:              "s3://rainpulse/radar/qc/z9598/scan/rp008-basic-1.0.4/volume.zarr",
		CurrentStatus:      workflow.RadarScanQCReady,
		GridID:             "fuzhou_118_123_25_27_0p01deg_v1",
		GridConfigVersion:  "fuzhou-grid-0p01deg-v1",
		GridProfileVersion: "rp009-hybrid-v1",
		HybridScanVersion:  "hybrid-scan-1.0.0",
		GridConfig:         json.RawMessage(`{"profile_version":"rp009-hybrid-v1"}`),
		GridConfigSHA256:   "63266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f678",
	}

	job, err := service.CreateRadarGrid(context.Background(), input)
	if err != nil {
		t.Fatalf("CreateRadarGrid() error = %v", err)
	}
	first := repository.radarGrid
	if first.Outbox.Subject != RadarGridRequestedSubject ||
		first.Outbox.EventType != RadarGridRequestedEventType {
		t.Fatalf("unexpected radar grid worker route: %#v", first.Outbox)
	}
	if job.JobType != RadarGridJobType || job.ConfigVersion != input.GridProfileVersion {
		t.Fatalf("unexpected radar grid job: %#v", job)
	}
	var requested RadarGridRequested
	if err := json.Unmarshal(first.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode radar grid request: %v", err)
	}
	if requested.Payload.ScanID != input.ScanID || requested.Payload.InputURI != input.QCURI ||
		requested.Payload.GridID != input.GridID {
		t.Fatalf("unexpected radar grid request: %#v", requested)
	}
	wantPrefix := "s3://rainpulse/radar/grid/z9598/" + input.ScanID.String() +
		"/hybrid-scan-1.0.0/"
	if requested.Payload.OutputPrefix != wantPrefix {
		t.Fatalf("radar grid output must be algorithm-version isolated: %q", requested.Payload.OutputPrefix)
	}
	second, err := service.CreateRadarGrid(context.Background(), input)
	if err != nil {
		t.Fatalf("repeat CreateRadarGrid() error = %v", err)
	}
	if second.ID != job.ID || repository.radarGrid.Outbox.ID != first.Outbox.ID {
		t.Fatal("radar grid workflow identifiers are not deterministic")
	}

	input.CurrentStatus = workflow.RadarScanNormalized
	if _, err := service.CreateRadarGrid(context.Background(), input); err == nil {
		t.Fatal("normalized radar scan must pass QC before gridding")
	}
}

func TestCreateAnalysisMosaicAlignsClosestReadyGridAndUsesV2Contract(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 25, 12, 6, 0, 0, time.UTC)
	analysisTime := time.Date(2026, 8, 25, 12, 5, 0, 0, time.UTC)
	service := NewService(repository, Options{Now: func() time.Time { return now }})
	nearScan := uuid.MustParse("10000000-0000-4000-8000-000000000005")
	farScan := uuid.MustParse("10000000-0000-4000-8000-000000000006")
	input := AnalysisMosaicInput{
		AnalysisTime:           analysisTime,
		GridID:                 "fuzhou_118_123_25_27_0p01deg_v1",
		GridConfigVersion:      "fuzhou-grid-0p01deg-v1",
		MosaicConfigVersion:    "rp016-qi-mosaic-v1",
		MosaicAlgorithmVersion: "qi-mosaic-1.1.0",
		FlagDefinitionVersion:  "qc-flags-v1",
		MaximumAbsoluteOffset:  150 * time.Second,
		MinimumContributors:    1,
		Candidates: []AnalysisMosaicCandidate{
			{
				RadarID: "z9598", ScanID: farScan,
				GridURI:           "s3://rainpulse/radar/grid/z9598/far/grid.zarr",
				VolumeEndTime:     analysisTime.Add(-120 * time.Second),
				CurrentStatus:     workflow.RadarScanGridReady,
				HybridScanVersion: "hybrid-scan-1.1.0",
			},
			{
				RadarID: "z9598", ScanID: nearScan,
				GridURI:           "s3://rainpulse/radar/grid/z9598/near/grid.zarr",
				VolumeEndTime:     analysisTime.Add(-18 * time.Second),
				CurrentStatus:     workflow.RadarScanGridReady,
				HybridScanVersion: "hybrid-scan-1.1.0",
			},
		},
		MosaicConfig:       json.RawMessage(`{"profile_version":"rp016-qi-mosaic-v1"}`),
		MosaicConfigSHA256: "63266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f678",
	}

	analysis, job, err := service.CreateAnalysisMosaic(context.Background(), input)
	if err != nil {
		t.Fatalf("CreateAnalysisMosaic() error = %v", err)
	}
	if analysis.Status != workflow.AnalysisMosaic || job.JobType != AnalysisMosaicJobType {
		t.Fatalf("unexpected mosaic workflow state: analysis=%s job=%s", analysis.Status, job.JobType)
	}
	if repository.analysisMosaic.Outbox.Subject != AnalysisMosaicRequestedSubject ||
		repository.analysisMosaic.Outbox.EventType != AnalysisMosaicRequestedEventType {
		t.Fatalf("unexpected mosaic worker route: %#v", repository.analysisMosaic.Outbox)
	}
	var requested AnalysisMosaicRequested
	if err := json.Unmarshal(repository.analysisMosaic.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode mosaic request: %v", err)
	}
	if len(requested.Payload.Inputs) != 1 || requested.Payload.Inputs[0].ScanID != nearScan ||
		requested.Payload.Inputs[0].TimeOffsetSeconds != -18 {
		t.Fatalf("mosaic did not select the closest ready grid: %#v", requested.Payload.Inputs)
	}
	if requested.Payload.MosaicAlgorithm != input.MosaicAlgorithmVersion ||
		requested.Payload.OutputPrefix != "s3://rainpulse/analysis/mosaic/"+
			input.GridID+"/2026/08/25/120500Z/qi-mosaic-1.1.0/" {
		t.Fatalf("unexpected mosaic request: %#v", requested.Payload)
	}
	secondAnalysis, secondJob, err := service.CreateAnalysisMosaic(context.Background(), input)
	if err != nil {
		t.Fatalf("repeat CreateAnalysisMosaic() error = %v", err)
	}
	if secondAnalysis.ID != analysis.ID || secondJob.ID != job.ID {
		t.Fatal("analysis mosaic workflow identifiers are not deterministic")
	}
}

func TestCreateAnalysisQPEUsesCommittedMosaicAndDeterministicIDs(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 25, 12, 5, 3, 0, time.UTC)
	analysisTime := now.Truncate(5 * time.Minute)
	service := NewService(repository, Options{Now: func() time.Time { return now }})
	input := AnalysisQPEInput{
		AnalysisID:             uuid.MustParse("75000000-0000-4000-8000-000000000001"),
		RunID:                  uuid.MustParse("72000000-0000-4000-8000-000000000001"),
		AnalysisTime:           analysisTime,
		GridID:                 "fuzhou_118_123_25_27_0p01deg_v1",
		GridConfigVersion:      "fuzhou-grid-0p01deg-v1",
		MosaicConfigVersion:    "rp016-qi-mosaic-v1",
		MosaicAlgorithmVersion: "qi-mosaic-1.1.0",
		FlagDefinitionVersion:  "qc-flags-v1",
		MosaicURI:              "s3://rainpulse/analysis/mosaic/fixture/mosaic.zarr",
		CurrentStatus:          workflow.AnalysisQPE,
		QPEConfigVersion:       "rp011-basic-qpe-v1",
		QPEAlgorithmVersion:    "basic-zr-qpe-1.0.0",
		QPEConfig:              json.RawMessage(`{"profile_version":"rp011-basic-qpe-v1"}`),
		QPEConfigSHA256:        "63266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f678",
	}

	job, err := service.CreateAnalysisQPE(context.Background(), input)
	if err != nil {
		t.Fatalf("CreateAnalysisQPE() error = %v", err)
	}
	if job.JobType != AnalysisQPEJobType ||
		repository.analysisQPE.Outbox.Subject != AnalysisQPERequestedSubject {
		t.Fatalf("unexpected QPE route: job=%s outbox=%#v", job.JobType, repository.analysisQPE.Outbox)
	}
	var requested AnalysisQPERequested
	if err := json.Unmarshal(repository.analysisQPE.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode QPE request: %v", err)
	}
	if requested.Payload.InputURI != input.MosaicURI ||
		requested.Payload.OutputPrefix != "s3://rainpulse/analysis/"+input.GridID+
			"/2026/08/25/120500Z/basic-zr-qpe-1.0.0/" ||
		requested.Payload.QPEConfigVersion != input.QPEConfigVersion {
		t.Fatalf("unexpected QPE request: %#v", requested.Payload)
	}
	second, err := service.CreateAnalysisQPE(context.Background(), input)
	if err != nil {
		t.Fatalf("repeat CreateAnalysisQPE() error = %v", err)
	}
	if second.ID != job.ID {
		t.Fatal("analysis QPE job identifier is not deterministic")
	}
}

func TestCreateAnalysisDiagnosticsUsesReadyAnalysisAndExactQCRadars(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 25, 12, 6, 0, 0, time.UTC)
	service := NewService(repository, Options{Now: func() time.Time { return now }})
	input := AnalysisDiagnosticsInput{
		AnalysisID:    uuid.MustParse("75000000-0000-4000-8000-000000000001"),
		RunID:         uuid.MustParse("72000000-0000-4000-8000-000000000001"),
		AnalysisTime:  now.Truncate(5 * time.Minute),
		GridID:        "fuzhou_118_123_25_27_0p01deg_v1",
		AnalysisURI:   "s3://rainpulse/analysis/fixture/analysis.zarr",
		CurrentStatus: workflow.AnalysisReady,
		RadarInputs: []workflow.AnalysisDiagnosticRadarInput{
			{
				RadarID: "z9598",
				ScanID:  uuid.MustParse("86000000-0000-4000-8000-000000000001"),
				QCURI:   "s3://rainpulse/radar/qc/z9598/fixture/volume.zarr",
			},
		},
		DiagnosticConfig: json.RawMessage(
			`{"profile_version":"rp012-operational-diagnostics-v1"}`,
		),
		DiagnosticConfigSHA256:  "73266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f679",
		DiagnosticConfigVersion: "rp012-operational-diagnostics-v1",
		RendererVersion:         "radar-diagnostic-renderer-1.0.0",
		FlagDefinitionVersion:   "qc-flags-v1",
	}

	job, err := service.CreateAnalysisDiagnostics(context.Background(), input)
	if err != nil {
		t.Fatalf("CreateAnalysisDiagnostics() error = %v", err)
	}
	if job.JobType != AnalysisDiagnosticsJobType ||
		repository.analysisDiagnostics.Outbox.Subject != AnalysisDiagnosticsRequestedSubject {
		t.Fatalf("unexpected diagnostic route: job=%s", job.JobType)
	}
	var requested AnalysisDiagnosticsRequested
	if err := json.Unmarshal(repository.analysisDiagnostics.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode diagnostic request: %v", err)
	}
	if requested.Payload.InputURI != input.AnalysisURI ||
		requested.Payload.OutputPrefix != "s3://rainpulse/diagnostics/"+
			input.AnalysisID.String()+"/radar-diagnostic-renderer-1.0.0/" ||
		len(requested.Payload.RadarInputs) != 1 ||
		requested.Payload.RadarInputs[0].QCURI != input.RadarInputs[0].QCURI {
		t.Fatalf("unexpected diagnostic request: %#v", requested.Payload)
	}
	second, err := service.CreateAnalysisDiagnostics(context.Background(), input)
	if err != nil || second.ID != job.ID {
		t.Fatalf("diagnostic replay = %#v, %v", second, err)
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

func TestCreateNowcastInputSelectsLatestContiguousOperationalFrames(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 25, 12, 11, 0, 0, time.UTC)
	issueTime := time.Date(2026, 8, 25, 12, 10, 0, 0, time.UTC)
	service := NewService(repository, Options{Now: func() time.Time { return now }})
	candidates := make([]NowcastInputCandidate, 3)
	for index := range candidates {
		analysisTime := issueTime.Add(time.Duration(index-2) * 5 * time.Minute)
		candidates[index] = NowcastInputCandidate{
			AnalysisID:    uuid.MustParse(fmt.Sprintf("81000000-0000-4000-8000-%012d", index+1)),
			AnalysisTime:  analysisTime,
			GridID:        "fuzhou_118_123_25_27_0p01deg_v1",
			AnalysisURI:   fmt.Sprintf("s3://rainpulse/analysis/%d/analysis.zarr", index),
			CurrentStatus: workflow.AnalysisReady, OperationalEligible: true,
			ValidCoverageRatio: 0.8, MeanQualityIndex: 0.7,
		}
	}
	input := NowcastInputInput{
		IssueTime: issueTime, GridID: "fuzhou_118_123_25_27_0p01deg_v1",
		GridConfigVersion: "fuzhou-grid-0p01deg-v1",
		PreprocessVersion: "nowcast-input-builder-1.0.0",
		GateConfigVersion: "rp013-fixed-5min-v1",
		MinimumFrames:     3, MaximumFrames: 6, Timestep: 5 * time.Minute,
		MinimumValidCoverageRatio: 0.7, MinimumMeanQualityIndex: 0.45,
		Candidates: candidates, Config: json.RawMessage(`{"profile_version":"rp013-fixed-5min-v1"}`),
		ConfigSHA256: "63266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f678",
	}

	run, job, err := service.CreateNowcastInput(context.Background(), input)
	if err != nil {
		t.Fatalf("CreateNowcastInput() error = %v", err)
	}
	if run.Status != workflow.RunPreprocessing || job.JobType != NowcastInputJobType {
		t.Fatalf("unexpected RP-013 workflow state: run=%s job=%s", run.Status, job.JobType)
	}
	if len(repository.nowcastInput.Frames) != 3 ||
		!repository.nowcastInput.Frames[2].AnalysisTime.Equal(issueTime) {
		t.Fatalf("unexpected selected sequence: %#v", repository.nowcastInput.Frames)
	}
	var requested NowcastInputRequested
	if err := json.Unmarshal(repository.nowcastInput.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode NowcastInput request: %v", err)
	}
	if requested.EventType != NowcastInputRequestedEventType ||
		repository.nowcastInput.Outbox.Subject != NowcastInputRequestedSubject ||
		len(requested.Payload.AnalysisIDs) != 3 {
		t.Fatalf("unexpected RP-013 request: %#v", requested)
	}
	secondRun, secondJob, err := service.CreateNowcastInput(context.Background(), input)
	if err != nil || secondRun.ID != run.ID || secondJob.ID != job.ID {
		t.Fatalf("NowcastInput identity is not deterministic: %v", err)
	}

	input.Candidates[1].AnalysisTime = issueTime.Add(-15 * time.Minute)
	if _, _, err := service.CreateNowcastInput(context.Background(), input); err == nil {
		t.Fatal("gapped RadarAnalysis sequence must be rejected")
	}
	input.Candidates[1].AnalysisTime = issueTime.Add(-5 * time.Minute)
	input.Candidates[2].MeanQualityIndex = 0.2
	if _, _, err := service.CreateNowcastInput(context.Background(), input); err == nil {
		t.Fatal("below-gate RadarAnalysis must be rejected")
	}
}

func TestCreatePystepsLKSchedulesOnlyCommittedInputReadyRun(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 25, 12, 12, 0, 0, time.UTC)
	issueTime := time.Date(2026, 8, 25, 12, 10, 0, 0, time.UTC)
	service := NewService(repository, Options{Now: func() time.Time { return now }})
	input := PystepsLKInput{
		RunID:             uuid.MustParse("a78aa324-0832-59e1-b9ea-d97933b2821e"),
		NowcastInputJobID: uuid.MustParse("498308d8-994e-522b-b976-e1e9ce242e6f"),
		IssueTime:         issueTime,
		GridID:            "fuzhou_118_123_25_27_0p01deg_v1",
		CurrentStatus:     workflow.RunInputReady,
		InputURI:          "s3://rainpulse/nowcast-input/test/input.zarr",
		InputAssetIDs: []uuid.UUID{
			uuid.MustParse("81300000-0000-4000-8000-000000000001"),
			uuid.MustParse("81300000-0000-4000-8000-000000000002"),
			uuid.MustParse("81300000-0000-4000-8000-000000000003"),
		},
		ModelID: PystepsLKModelID, ModelVersion: PystepsLKModelVersion,
		ConfigVersion: "rp016-pysteps-lk-v1", ForecastContractVersion: "1.1",
		BaselineModels: []string{"persistence", "translation"},
		Config:         json.RawMessage(`{"profile_version":"rp016-pysteps-lk-v1"}`),
		ConfigSHA256:   "63266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f678",
	}

	run, job, err := service.CreatePystepsLK(context.Background(), input)
	if err != nil {
		t.Fatalf("CreatePystepsLK() error = %v", err)
	}
	if run.Status != workflow.RunBaselineRunning || job.JobType != PystepsLKJobType ||
		job.ModelID != PystepsLKModelID {
		t.Fatalf("unexpected pySTEPS-LK workflow state: run=%s job=%#v", run.Status, job)
	}
	var requested PystepsLKRequested
	if err := json.Unmarshal(repository.pystepsLK.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode pySTEPS-LK request: %v", err)
	}
	if requested.EventType != PystepsLKRequestedEventType ||
		repository.pystepsLK.Outbox.Subject != PystepsLKRequestedSubject ||
		requested.Payload.ForecastContractVersion != "1.1" ||
		len(requested.Payload.InputAssetIDs) != 3 {
		t.Fatalf("unexpected pySTEPS-LK request: %#v", requested)
	}
	secondRun, secondJob, err := service.CreatePystepsLK(context.Background(), input)
	if err != nil || secondRun.ID != run.ID || secondJob.ID != job.ID {
		t.Fatalf("pySTEPS-LK identity is not deterministic: %v", err)
	}

	input.CurrentStatus = workflow.RunPreprocessing
	if _, _, err := service.CreatePystepsLK(context.Background(), input); err == nil {
		t.Fatal("pySTEPS-LK must reject a run that is not INPUT_READY")
	}
}

func TestCreateProductBuildSchedulesThreeProductsFromCommittedBaseline(t *testing.T) {
	repository := &fakeRepository{}
	now := time.Date(2026, 8, 25, 12, 10, 2, 0, time.UTC)
	service := NewService(repository, Options{Now: func() time.Time { return now }})
	input := ProductBuildInput{
		RunID:                 uuid.MustParse("97000000-0000-4000-8000-000000000001"),
		ModelRunID:            uuid.MustParse("97000000-0000-4000-8000-000000000002"),
		IssueTime:             now.Truncate(5 * time.Minute),
		GridID:                "fuzhou_118_123_25_27_0p01deg_v1",
		CurrentStatus:         workflow.RunBaselineReady,
		ForecastURI:           "s3://rainpulse/products/run/pysteps-lk/pysteps-lk-1.1.0/forecast.zarr",
		ForecastSHA256:        strings.Repeat("a", 64),
		InputAssetIDs:         []uuid.UUID{uuid.MustParse("97000000-0000-4000-8000-000000000003")},
		ModelID:               PystepsLKModelID,
		ModelVersion:          PystepsLKModelVersion,
		ModelConfigVersion:    "rp016-pysteps-lk-v1",
		ProductConfigVersion:  "rp015-application-products-v1",
		ProductBundleContract: "1.0",
		ProductConfig:         json.RawMessage(`{"profile_version":"rp015-application-products-v1"}`),
		ProductConfigSHA256:   strings.Repeat("b", 64),
	}

	run, job, err := service.CreateProductBuild(context.Background(), input)
	if err != nil {
		t.Fatalf("CreateProductBuild() error = %v", err)
	}
	if run.Status != workflow.RunProductBuilding || job.JobType != ProductBuildJobType {
		t.Fatalf("unexpected product workflow state: run=%s job=%s", run.Status, job.JobType)
	}
	var requested ProductBuildRequested
	if err := json.Unmarshal(repository.productBuild.Outbox.Payload, &requested); err != nil {
		t.Fatalf("decode product request: %v", err)
	}
	if requested.EventType != ProductBuildRequestedEventType ||
		repository.productBuild.Outbox.Subject != ProductBuildRequestedSubject ||
		requested.Payload.InputSHA256 != input.ForecastSHA256 ||
		requested.Payload.ProductIDs.RainRate == uuid.Nil ||
		requested.Payload.ProductIDs.Accumulation60 == uuid.Nil ||
		requested.Payload.ProductIDs.Accumulation120 == uuid.Nil {
		t.Fatalf("unexpected product request: %#v", requested)
	}
	if !strings.HasSuffix(
		requested.Payload.OutputPrefix,
		"/distribution/rp015-application-products-v1/",
	) {
		t.Fatalf("unexpected product output prefix: %s", requested.Payload.OutputPrefix)
	}
	secondRun, secondJob, err := service.CreateProductBuild(context.Background(), input)
	if err != nil || secondRun.ID != run.ID || secondJob.ID != job.ID {
		t.Fatalf("product workflow identifiers are not deterministic: %v", err)
	}
	input.CurrentStatus = workflow.RunInputReady
	if _, _, err := service.CreateProductBuild(context.Background(), input); err == nil {
		t.Fatal("product build accepted a run before BASELINE_READY")
	}
}

type fakeRepository struct {
	created             workflow.CreateBundle
	radarDecode         workflow.RadarDecodeBundle
	radarQC             workflow.RadarQCBundle
	radarGrid           workflow.RadarGridBundle
	analysisMosaic      workflow.AnalysisMosaicBundle
	analysisQPE         workflow.AnalysisQPEBundle
	analysisDiagnostics workflow.AnalysisDiagnosticsBundle
	nowcastInput        workflow.NowcastInputBundle
	pystepsLK           workflow.PystepsLKBundle
	productBuild        workflow.ProductBuildBundle
	domain              workflow.DomainSimulation
	claimed             workflow.OutboxEvent
	published           uuid.UUID
	failed              uuid.UUID
	appliedFailure      JobFailed
}

func (repository *fakeRepository) CreateNowcastInputBundle(
	_ context.Context,
	bundle workflow.NowcastInputBundle,
) error {
	repository.nowcastInput = bundle
	return nil
}

func (repository *fakeRepository) CreatePystepsLKBundle(
	_ context.Context,
	bundle workflow.PystepsLKBundle,
) error {
	repository.pystepsLK = bundle
	return nil
}

func (repository *fakeRepository) CreateProductBuildBundle(
	_ context.Context,
	bundle workflow.ProductBuildBundle,
) error {
	repository.productBuild = bundle
	return nil
}

func (repository *fakeRepository) CreateAnalysisMosaicBundle(
	_ context.Context,
	bundle workflow.AnalysisMosaicBundle,
) error {
	repository.analysisMosaic = bundle
	return nil
}

func (repository *fakeRepository) CreateAnalysisQPEBundle(
	_ context.Context,
	bundle workflow.AnalysisQPEBundle,
) error {
	repository.analysisQPE = bundle
	return nil
}

func (repository *fakeRepository) CreateAnalysisDiagnosticsBundle(
	_ context.Context,
	bundle workflow.AnalysisDiagnosticsBundle,
) error {
	repository.analysisDiagnostics = bundle
	return nil
}

func (repository *fakeRepository) CreateRadarGridBundle(
	_ context.Context,
	bundle workflow.RadarGridBundle,
) error {
	repository.radarGrid = bundle
	return nil
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

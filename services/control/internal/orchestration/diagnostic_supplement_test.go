package orchestration

import (
	"context"
	"encoding/json"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"testing"
	"time"
)

func supplementInput() AnalysisDiagnosticsInput {
	return AnalysisDiagnosticsInput{AnalysisID: uuid.New(), RunID: uuid.New(), AnalysisTime: time.Now(), GridID: "grid", AnalysisURI: "s3://rainpulse/analysis/volume.zarr", CurrentStatus: workflow.AnalysisReady,
		RadarInputs:             []workflow.AnalysisDiagnosticRadarInput{{RadarID: "z9591", ScanID: uuid.New(), QCURI: "s3://rainpulse/qc/z9591"}},
		SupplementalRadarInputs: []workflow.AnalysisDiagnosticRadarInput{{RadarID: "z9595", ScanID: uuid.New(), QCURI: "s3://rainpulse/qc/z9595"}},
		SupplementalRevisionID:  uuid.New(), DiagnosticConfig: json.RawMessage(`{}`), DiagnosticConfigSHA256: "73266c7c72321262a01b945281060abd84153a8f3ad64a95c5b73b9fd510f679", DiagnosticConfigVersion: "diagnostics", RendererVersion: "renderer", FlagDefinitionVersion: "qc-flags-v2"}
}
func TestDiagnosticSupplementPreservesContributorBoundary(t *testing.T) {
	repository := &fakeRepository{}
	input := supplementInput()
	_, err := NewService(repository, Options{}).CreateAnalysisDiagnostics(context.Background(), input)
	if err != nil {
		t.Fatal(err)
	}
	b := repository.analysisDiagnostics
	if len(b.RadarInputs) != 1 || len(b.SupplementalRadarInputs) != 1 || b.RegenerationRequestID != nil {
		t.Fatal("supplement must remain separate from contributors and pipeline requests")
	}
	var request AnalysisDiagnosticsRequested
	if err := json.Unmarshal(b.Job.RequestPayload, &request); err != nil {
		t.Fatal(err)
	}
	if len(request.Payload.RadarInputs) != 2 {
		t.Fatal("renderer must receive both native inputs")
	}
	if len(input.RadarInputs) != 1 {
		t.Fatal("input caller was mutated")
	}
}
func TestDiagnosticSupplementRejectsAmbiguousIdentity(t *testing.T) {
	for _, mode := range []string{"duplicate", "missing-revision", "no-contributor"} {
		t.Run(mode, func(t *testing.T) {
			in := supplementInput()
			switch mode {
			case "duplicate":
				in.SupplementalRadarInputs[0] = in.RadarInputs[0]
			case "missing-revision":
				in.SupplementalRevisionID = uuid.Nil
			case "no-contributor":
				in.RadarInputs = nil
			}
			if validateAnalysisDiagnosticsInput(in) == nil {
				t.Fatal("ambiguous supplemental input accepted")
			}
		})
	}
}

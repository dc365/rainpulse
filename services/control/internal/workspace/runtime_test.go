package workspace

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

type runtimeFakeStore struct {
	diagnostics workflow.AnalysisDiagnostics
	snapshot    PipelineSnapshot
	cancelled   RegenerationCancellation
}

func (store *runtimeFakeStore) GetAnalysisDiagnosticsByJob(context.Context, uuid.UUID) (workflow.AnalysisDiagnostics, error) {
	return store.diagnostics, nil
}
func (*runtimeFakeStore) GetProduct(context.Context, uuid.UUID) (workflow.Product, error) {
	return workflow.Product{}, workflow.ErrNotFound
}
func (*runtimeFakeStore) GetProductAsset(context.Context, uuid.UUID, uuid.UUID) (workflow.ProductAsset, error) {
	return workflow.ProductAsset{}, workflow.ErrNotFound
}
func (*runtimeFakeStore) ListProductAssets(context.Context, uuid.UUID) ([]workflow.ProductAsset, error) {
	return nil, workflow.ErrNotFound
}
func (store *runtimeFakeStore) WorkspacePipelineSnapshot(context.Context, string, time.Time) (PipelineSnapshot, error) {
	return store.snapshot, nil
}
func (store *runtimeFakeStore) CancelWorkspaceRegeneration(_ context.Context, requestID uuid.UUID, reason string) (RegenerationCancellation, error) {
	store.cancelled = RegenerationCancellation{RequestID: requestID, Status: "CANCELLED", Reason: reason}
	return store.cancelled, nil
}

type runtimeFakeObjects struct {
	manifest []byte
	index    []byte
}

func (objects *runtimeFakeObjects) Read(_ context.Context, _ string, relative string) ([]byte, string, error) {
	if relative == "manifest.json" {
		return objects.manifest, "", nil
	}
	return objects.index, "", nil
}
func (*runtimeFakeObjects) ReadObject(context.Context, string, int64) ([]byte, string, error) {
	return nil, "", workflow.ErrNotFound
}
func (*runtimeFakeObjects) ReadRange(context.Context, string, int64, int64) ([]byte, int64, string, error) {
	return nil, 0, "", workflow.ErrNotFound
}

func qpePointFixture() []byte {
	data := make([]byte, 64+2*2*5)
	copy(data[:8], []byte{'R', 'P', 'P', 'N', 'T', 'V', '1', 0})
	binary.BigEndian.PutUint16(data[8:10], 2)
	binary.BigEndian.PutUint16(data[10:12], 2)
	binary.BigEndian.PutUint16(data[12:14], 1)
	binary.BigEndian.PutUint16(data[14:16], 5)
	binary.BigEndian.PutUint64(data[16:24], math.Float64bits(118))
	binary.BigEndian.PutUint64(data[24:32], math.Float64bits(25))
	binary.BigEndian.PutUint64(data[32:40], math.Float64bits(0.01))
	binary.BigEndian.PutUint64(data[40:48], math.Float64bits(0.01))
	for cell, rate := range []float32{0, 2.5, 4, 8.5} {
		offset := 64 + cell*5
		binary.BigEndian.PutUint32(data[offset:offset+4], math.Float32bits(rate))
		data[offset+4] = 127
	}
	return data
}

func TestRuntimeExactSampleReturnsQPEGridValue(t *testing.T) {
	index := qpePointFixture()
	manifest, _ := json.Marshal(map[string]any{
		"point_queries": map[string]any{
			"grid-rate-qpe": map[string]any{
				"object_path":  "query/grid-rate-qpe-point-index.bin",
				"sha256":       responseETag(index)[1:65],
				"size_bytes":   len(index),
				"unit":         "mm/h",
				"lead_minutes": []int{0},
				"valid_times":  []string{"2026-08-28T02:30:00Z"},
				"frame_kinds":  []string{"analysis"},
			},
		},
	})
	store := &runtimeFakeStore{diagnostics: workflow.AnalysisDiagnostics{
		JobID:     uuid.MustParse("82600000-0000-4000-8000-000000000001"),
		BundleURI: "s3://rainpulse/diagnostics/a",
	}}
	handler := &runtimeHandler{
		next: http.NotFoundHandler(), workspace: http.NotFoundHandler(),
		store: store, objects: &runtimeFakeObjects{manifest: manifest, index: index}, now: time.Now,
	}
	asset := "/api/v1/diagnostics/82600000-0000-4000-8000-000000000001/layers/grid-rate-qpe"
	request := httptest.NewRequest(http.MethodGet, workspaceSamplePath+"?asset_url="+url.QueryEscape(asset)+"&longitude=118.01&latitude=25.01", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("response = %d: %s", response.Code, response.Body.String())
	}
	var sample ExactSample
	if err := json.Unmarshal(response.Body.Bytes(), &sample); err != nil {
		t.Fatal(err)
	}
	if !sample.Valid || sample.Value == nil || *sample.Value != 8.5 || sample.FrameKind != "analysis" {
		t.Fatalf("unexpected sample: %#v", sample)
	}
}

func TestRuntimeCancellationRequiresAdministrator(t *testing.T) {
	store := &runtimeFakeStore{}
	handler := &runtimeHandler{
		next: http.NotFoundHandler(), workspace: http.NotFoundHandler(),
		store: store, adminToken: "secret", now: time.Now,
	}
	path := workspaceCancelPrefix + "82600000-0000-4000-8000-000000000001" + workspaceCancelSuffix
	request := httptest.NewRequest(http.MethodPost, path, nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("response = %d", response.Code)
	}
}

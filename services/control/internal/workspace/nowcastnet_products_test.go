package workspace

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"testing"
	"time"

	nowcastnetproducts "github.com/fonwee/rainpulse-nowcast/services/control/internal/nowcastnetproducts"
	"github.com/google/uuid"
)

type formalNowcastNetRunStoreFake struct {
	runs []NowcastNetAlgorithmRun
}

func (store formalNowcastNetRunStoreFake) ListCompletedNowcastNetAlgorithmRuns(
	context.Context,
	int,
) ([]NowcastNetAlgorithmRun, error) {
	return store.runs, nil
}

type formalNowcastNetObjectReaderFake struct {
	objects map[string][]byte
}

func (reader formalNowcastNetObjectReaderFake) Read(
	_ context.Context,
	uri string,
	path string,
) ([]byte, string, error) {
	data, ok := reader.objects[uri+"/"+path]
	if !ok {
		return nil, "", nowcastnetproducts.ErrNotFound
	}
	return data, fmt.Sprintf("%x", sha256.Sum256(data)), nil
}

func (formalNowcastNetObjectReaderFake) ReadObject(context.Context, string, int64) ([]byte, string, error) {
	return nil, "", nowcastnetproducts.ErrNotFound
}

func (formalNowcastNetObjectReaderFake) ReadRange(context.Context, string, int64, int64) ([]byte, int64, string, error) {
	return nil, 0, "", nowcastnetproducts.ErrNotFound
}

func TestFormalNowcastNetProductStoreReadsAuthoritativeArtifact(t *testing.T) {
	issueTime := time.Date(2026, 8, 28, 2, 25, 0, 0, time.UTC)
	runID := uuid.MustParse("c0000000-0000-4000-8000-000000000001")
	jobID := uuid.MustParse("c0000000-0000-4000-8000-000000000002")
	algorithmRunID := uuid.MustParse("c0000000-0000-4000-8000-000000000003")
	uri := "s3://rainpulse/products/test/nowcastnet/shadow/nowcastnet-shadow-products"
	payload := []byte("formal-frame")
	digest := fmt.Sprintf("%x", sha256.Sum256(payload))
	bundle := nowcastnetproducts.Bundle{
		ContractName: "rainpulse.nowcastnet-shadow-product-bundle", ContractVersion: "1.2",
		BundleID: jobID, RunID: runID, JobID: jobID, AlgorithmRunID: algorithmRunID,
		IssueTime: issueTime, GridID: "fuzhou-grid", GridConfigVersion: "grid-v1",
		ModelID: "nowcastnet", ModelVersion: "public-v1", ProfileVersion: "shadow-v2",
		MemberCount: 4, CadenceMinutes: 5, Lifecycle: "shadow", Width: 32, Height: 32,
		Bounds: [4]float64{118, 25, 118.32, 25.32}, LegendUnit: "mm/h",
		Legend:    []nowcastnetproducts.LegendEntry{{Minimum: 0.1, Color: "#9dd9ff"}, {Minimum: 1, Color: "#4ba3f2"}},
		CreatedAt: issueTime.Add(time.Minute),
	}
	for lead := 5; lead <= 120; lead += 5 {
		frame := nowcastnetproducts.Frame{
			AssetID:    fmt.Sprintf("ensemble-mean-lead-%03d-png", lead),
			ObjectPath: fmt.Sprintf("rain_rate/lead-%03d/layer.png", lead),
			MediaType:  "image/png", SHA256: digest, SizeBytes: int64(len(payload)), LeadMinutes: lead,
			ValidTime: issueTime.Add(time.Duration(lead) * time.Minute), Unit: "mm/h",
			CoverageRatio: 1, ValidCellCount: 1024, Bounds: bundle.Bounds,
		}
		if lead%10 == 0 {
			frame.FrameKind, frame.SourceLeads = "native", []int{lead}
		} else {
			frame.FrameKind = "derived"
			frame.Derivation = "bidirectional-dense-optical-flow-advection-v1"
			frame.SourceLeads = []int{lead - 5, lead + 5}
		}
		bundle.Frames = append(bundle.Frames, frame)
	}
	manifest, err := json.Marshal(bundle)
	if err != nil {
		t.Fatal(err)
	}
	objects := formalNowcastNetObjectReaderFake{objects: map[string][]byte{
		uri + "/manifest.json":                  manifest,
		uri + "/" + bundle.Frames[0].ObjectPath: payload,
	}}
	store := newFormalNowcastNetProductStore(
		formalNowcastNetRunStoreFake{runs: []NowcastNetAlgorithmRun{{
			AlgorithmRunID: algorithmRunID, RunID: runID, JobID: jobID,
			IssueTime: issueTime, GridID: "fuzhou-grid", OutputURI: uri, CompletedAt: issueTime.Add(time.Minute),
		}}},
		objects,
	)
	cycles, err := store.ListCycles(context.Background())
	if err != nil || len(cycles) != 1 || cycles[0].BundleID != jobID {
		t.Fatalf("formal cycles = %#v, error = %v", cycles, err)
	}
	asset, err := store.ReadAsset(context.Background(), jobID.String(), bundle.Frames[0].AssetID)
	if err != nil || string(asset.Data) != string(payload) || asset.SHA256 != digest {
		t.Fatalf("formal asset = %#v, error = %v", asset, err)
	}
}

package nowcastnetproducts

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestFileStoreListsCycleAndVerifiesAsset(t *testing.T) {
	root := t.TempDir()
	bundleID := uuid.New()
	directory := filepath.Join(root, bundleID.String())
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	payload := testPNG(t, 128, 64)
	digest := fmt.Sprintf("%x", sha256.Sum256(payload))
	issueTime := time.Date(2026, 8, 28, 8, 30, 0, 0, time.UTC)
	bundle := Bundle{
		ContractName: "rainpulse.nowcastnet-shadow-product-bundle", ContractVersion: "1.0",
		BundleID: bundleID, IssueTime: issueTime, GridID: "fuzhou-grid",
		GridConfigVersion: "grid-v1", ModelID: "nowcastnet", ModelVersion: "public-v1",
		ProfileVersion: "shadow-v1", MemberCount: 4, CadenceMinutes: 10,
		Lifecycle: "shadow", ROI: ROI{YStart: 97, Height: 64, Width: 128},
		LegendUnit: "mm/h", Legend: []LegendEntry{{Minimum: 0.1, Color: "#9dd9ff"}, {Minimum: 1, Color: "#4ba3f2"}},
		CreatedAt: issueTime.Add(24 * time.Hour),
	}
	for lead := 10; lead <= 120; lead += 10 {
		assetID := fmt.Sprintf("ensemble-mean-lead-%03d-png", lead)
		objectPath := fmt.Sprintf("lead-%03d/ensemble-mean.png", lead)
		assetPath := filepath.Join(directory, filepath.FromSlash(objectPath))
		if err := os.MkdirAll(filepath.Dir(assetPath), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(assetPath, payload, 0o644); err != nil {
			t.Fatal(err)
		}
		bundle.Frames = append(bundle.Frames, Frame{
			AssetID: assetID, ObjectPath: objectPath, MediaType: "image/png",
			SHA256: digest, SizeBytes: int64(len(payload)), LeadMinutes: lead,
			ValidTime: issueTime.Add(time.Duration(lead) * time.Minute), Unit: "mm/h",
			CoverageRatio: 1, ValidCellCount: 64 * 128,
			Bounds: [4]float64{117.995, 25.965, 119.275, 26.605},
		})
	}
	manifest, err := json.Marshal(bundle)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), manifest, 0o644); err != nil {
		t.Fatal(err)
	}

	store := NewFileStore(root)
	cycles, err := store.ListCycles(context.Background())
	if err != nil || len(cycles) != 1 || cycles[0].BundleID != bundleID {
		t.Fatalf("cycles = %+v, error = %v", cycles, err)
	}
	selected, err := store.GetByCycle(context.Background(), issueTime, "fuzhou-grid")
	if err != nil || selected.BundleID != bundleID {
		t.Fatalf("selected = %+v, error = %v", selected, err)
	}
	asset, err := store.ReadAsset(context.Background(), bundleID.String(), bundle.Frames[0].AssetID)
	if err != nil || !bytes.Equal(asset.Data, payload) || asset.SHA256 != digest {
		t.Fatalf("asset = %+v, error = %v", asset, err)
	}
}

func TestFileStoreListsFullGridPartialCoverageCycle(t *testing.T) {
	root := t.TempDir()
	bundleID := uuid.New()
	directory := filepath.Join(root, bundleID.String())
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	const width = 501
	const height = 201
	const validCellCount = 33088
	cellCount := int64(width * height)
	payload := testPNG(t, width, height)
	digest := fmt.Sprintf("%x", sha256.Sum256(payload))
	issueTime := time.Date(2026, 8, 28, 8, 30, 0, 0, time.UTC)
	bounds := [4]float64{117.995, 24.995, 123.005, 27.005}
	bundle := Bundle{
		ContractName: "rainpulse.nowcastnet-shadow-product-bundle", ContractVersion: "1.1",
		BundleID: bundleID, IssueTime: issueTime, GridID: "fujian-operational-0p01-v1",
		GridConfigVersion: "grid-v1", ModelID: "nowcastnet", ModelVersion: "public-v1",
		ProfileVersion: "shadow-stitched-v1", MemberCount: 4, CadenceMinutes: 10,
		Lifecycle: "shadow", Width: width, Height: height, Bounds: bounds,
		LegendUnit: "mm/h", Legend: []LegendEntry{{Minimum: 0.1, Color: "#9dd9ff"}, {Minimum: 1, Color: "#4ba3f2"}},
		CreatedAt: issueTime.Add(24 * time.Hour),
	}
	for lead := 10; lead <= 120; lead += 10 {
		assetID := fmt.Sprintf("ensemble-mean-lead-%03d-png", lead)
		objectPath := fmt.Sprintf("lead-%03d/ensemble-mean.png", lead)
		assetPath := filepath.Join(directory, filepath.FromSlash(objectPath))
		if err := os.MkdirAll(filepath.Dir(assetPath), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(assetPath, payload, 0o644); err != nil {
			t.Fatal(err)
		}
		bundle.Frames = append(bundle.Frames, Frame{
			AssetID: assetID, ObjectPath: objectPath, MediaType: "image/png",
			SHA256: digest, SizeBytes: int64(len(payload)), LeadMinutes: lead,
			ValidTime: issueTime.Add(time.Duration(lead) * time.Minute), Unit: "mm/h",
			CoverageRatio:  float64(validCellCount) / float64(cellCount),
			ValidCellCount: validCellCount, MissingCellCount: cellCount - validCellCount,
			Bounds: bounds,
		})
	}
	manifest, err := json.Marshal(bundle)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), manifest, 0o644); err != nil {
		t.Fatal(err)
	}

	store := NewFileStore(root)
	cycles, err := store.ListCycles(context.Background())
	if err != nil || len(cycles) != 1 || cycles[0].ContractVersion != "1.1" {
		t.Fatalf("cycles = %+v, error = %v", cycles, err)
	}
	asset, err := store.ReadAsset(context.Background(), bundleID.String(), bundle.Frames[0].AssetID)
	if err != nil || !bytes.Equal(asset.Data, payload) {
		t.Fatalf("asset = %+v, error = %v", asset, err)
	}
}

func testPNG(t *testing.T, width, height int) []byte {
	t.Helper()
	canvas := image.NewRGBA(image.Rect(0, 0, width, height))
	canvas.Set(0, 0, color.RGBA{R: 10, G: 120, B: 200, A: 220})
	var buffer bytes.Buffer
	if err := png.Encode(&buffer, canvas); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

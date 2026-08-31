package ensembleproducts

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestFileStoreReturnsLatestBundleAndChecksAssetIntegrity(t *testing.T) {
	root := t.TempDir()
	older := "9b000000-0000-4000-8000-000000000001"
	newer := "9b000000-0000-4000-8000-000000000002"
	writeBundleFixture(t, root, older, time.Date(2026, 8, 29, 1, 0, 0, 0, time.UTC))
	writeBundleFixture(t, root, newer, time.Date(2026, 8, 29, 2, 0, 0, 0, time.UTC))
	store := NewFileStore(root)

	bundle, err := store.GetLatest(context.Background())
	if err != nil {
		t.Fatalf("get latest ensemble bundle: %v", err)
	}
	if bundle.BundleID.String() != newer || bundle.MemberCount != 12 || len(bundle.Layers) != 8 {
		t.Fatalf("unexpected latest bundle: %#v", bundle)
	}
	byCycle, err := store.GetByCycle(
		context.Background(),
		time.Date(2026, 8, 29, 0, 0, 0, 0, time.UTC),
		"fuzhou_118_123_25_27_0p01deg_v1",
	)
	if err != nil {
		t.Fatalf("get ensemble bundle by cycle: %v", err)
	}
	if byCycle.BundleID.String() != newer {
		t.Fatalf("expected newest matching cycle bundle, got %#v", byCycle)
	}
	if _, err := store.GetByCycle(
		context.Background(),
		time.Date(2026, 8, 29, 0, 5, 0, 0, time.UTC),
		"fuzhou_118_123_25_27_0p01deg_v1",
	); !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected missing cycle to be not found, got %v", err)
	}
	asset, err := store.ReadAsset(
		context.Background(), newer, "probability-gt-1-lead-005-png",
	)
	if err != nil {
		t.Fatalf("read ensemble asset: %v", err)
	}
	if asset.MediaType != "image/png" || !strings.HasPrefix(string(asset.Data), "\x89PNG") {
		t.Fatalf("unexpected asset: %#v", asset)
	}
}

func TestFileStoreFailsClosedForTraversalAndChecksumDrift(t *testing.T) {
	root := t.TempDir()
	bundleID := "9b000000-0000-4000-8000-000000000003"
	writeBundleFixture(t, root, bundleID, time.Now().UTC())
	store := NewFileStore(root)

	if _, err := store.ReadAsset(context.Background(), "../escape", "asset"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected traversal to be hidden, got %v", err)
	}
	path := filepath.Join(
		root, bundleID, "probability-gt-1", "lead-005", "layer.png",
	)
	if err := os.WriteFile(path, []byte("\x89PNG\r\n\x1a\nchanged"), 0o644); err != nil {
		t.Fatalf("tamper fixture: %v", err)
	}
	if _, err := store.ReadAsset(
		context.Background(), bundleID, "probability-gt-1-lead-005-png",
	); !errors.Is(err, ErrInvalidBundle) {
		t.Fatalf("expected checksum drift to fail closed, got %v", err)
	}
}

func TestFileStoreTreatsMissingRootAsNoOfflineBundle(t *testing.T) {
	_, err := NewFileStore(filepath.Join(t.TempDir(), "missing")).GetLatest(context.Background())
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected missing root to be not found, got %v", err)
	}
}

func writeBundleFixture(
	t *testing.T,
	root string,
	bundleID string,
	createdAt time.Time,
) {
	t.Helper()
	directory := filepath.Join(root, bundleID)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatalf("create fixture bundle: %v", err)
	}
	png := []byte("\x89PNG\r\n\x1a\nfixture")
	pngDigest := fmt.Sprintf("%x", sha256.Sum256(png))
	dummyDigest := strings.Repeat("a", 64)
	layerIDs := []string{
		"probability-gt-1", "probability-gt-5", "probability-gt-10",
		"probability-gt-20", "probability-gt-50",
		"quantile-p10", "quantile-p50", "quantile-p90",
	}
	issueTime := time.Date(2026, 8, 29, 0, 0, 0, 0, time.UTC)
	layers := make([]Layer, 0, len(layerIDs))
	for _, layerID := range layerIDs {
		probability := strings.HasPrefix(layerID, "probability")
		productType := "quantile"
		unit := "mm h-1"
		var threshold *float64
		var quantile *float64
		if probability {
			productType = "probability_exceedance"
			unit = "1"
			value := map[string]float64{
				"probability-gt-1": 1, "probability-gt-5": 5,
				"probability-gt-10": 10, "probability-gt-20": 20,
				"probability-gt-50": 50,
			}[layerID]
			threshold = &value
		} else {
			value := map[string]float64{
				"quantile-p10": 0.1, "quantile-p50": 0.5, "quantile-p90": 0.9,
			}[layerID]
			quantile = &value
		}
		layer := Layer{
			LayerID: layerID, ProductType: productType, VariableName: layerID,
			ThresholdMMH: threshold, Quantile: quantile, Unit: unit,
			Legend: []LegendEntry{{Minimum: 0.01, Color: "#d6eef7"}, {Minimum: 0.5, Color: "#2d8ea8"}},
		}
		for lead := 5; lead <= 120; lead += 5 {
			validTime := issueTime.Add(time.Duration(lead) * time.Minute)
			layer.ValidTimes = append(layer.ValidTimes, validTime)
			for _, format := range []struct {
				suffix, assetType, mediaType string
			}{
				{suffix: "png", assetType: "rendered_png", mediaType: "image/png"},
				{suffix: "nc", assetType: "application_netcdf", mediaType: "application/x-netcdf"},
			} {
				assetID := fmt.Sprintf("%s-lead-%03d-%s", layerID, lead, format.suffix)
				objectPath := fmt.Sprintf("%s/lead-%03d/layer.%s", layerID, lead, format.suffix)
				asset := Asset{
					AssetID: assetID, ObjectPath: objectPath, AssetType: format.assetType,
					MediaType: format.mediaType, SHA256: dummyDigest, SizeBytes: 8,
					LeadMinutes: lead, ValidTime: validTime, Unit: unit,
					CoverageRatio:  4096.0 / 100701.0,
					ValidCellCount: 4096, MissingCellCount: 96605,
				}
				if assetID == "probability-gt-1-lead-005-png" {
					asset.SHA256 = pngDigest
					asset.SizeBytes = int64(len(png))
					path := filepath.Join(directory, filepath.FromSlash(objectPath))
					if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
						t.Fatalf("create asset fixture directory: %v", err)
					}
					if err := os.WriteFile(path, png, 0o644); err != nil {
						t.Fatalf("write asset fixture: %v", err)
					}
				}
				layer.Assets = append(layer.Assets, asset)
			}
		}
		layers = append(layers, layer)
	}
	parsedID := uuid.MustParse(bundleID)
	bundle := Bundle{
		ContractName:    "rainpulse.ensemble-application-product-bundle",
		ContractVersion: "1.0", BundleID: parsedID, RunID: parsedID,
		JobID:     uuid.MustParse("9b000000-0000-4000-8000-000000000099"),
		IssueTime: issueTime, GridID: "fuzhou_118_123_25_27_0p01deg_v1",
		PixelEdgeBounds: []float64{117.995, 24.995, 123.005, 27.005},
		Width:           501, Height: 201,
		SourceForecast: SourceForecast{
			URI: "s3://rainpulse/forecast.zarr", SHA256: dummyDigest, ContractVersion: "1.2",
		},
		ModelID: "pysteps-steps", ModelVersion: "pysteps-steps-1.0.0",
		ModelConfigVersion:   "rp022-pysteps-steps-v1",
		ProductConfigVersion: "rp023-ensemble-application-products-v1",
		MemberCount:          12,
		CalibrationStatus:    "raw_ensemble_relative_frequency_uncalibrated",
		OperationalGate:      "independent_fujian_probabilistic_acceptance_required",
		Layers:               layers, CreatedAt: createdAt,
	}
	data, err := json.Marshal(bundle)
	if err != nil {
		t.Fatalf("marshal fixture: %v", err)
	}
	if err := os.WriteFile(filepath.Join(directory, "manifest.json"), data, 0o644); err != nil {
		t.Fatalf("write fixture manifest: %v", err)
	}
}

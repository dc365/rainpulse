package ensembleproducts

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
)

const (
	maximumManifestBytes = 4 << 20
	maximumAssetBytes    = 32 << 20
)

var (
	ErrNotFound      = errors.New("ensemble product bundle not found")
	ErrInvalidBundle = errors.New("ensemble product bundle is invalid")
)

type SourceForecast struct {
	URI             string `json:"uri"`
	SHA256          string `json:"sha256"`
	ContractVersion string `json:"contract_version"`
}

type LegendEntry struct {
	Minimum float64 `json:"minimum"`
	Color   string  `json:"color"`
}

type Asset struct {
	AssetID          string    `json:"asset_id"`
	ObjectPath       string    `json:"object_path"`
	AssetType        string    `json:"asset_type"`
	MediaType        string    `json:"media_type"`
	SHA256           string    `json:"sha256"`
	SizeBytes        int64     `json:"size_bytes"`
	LeadMinutes      int       `json:"lead_time_minutes"`
	ValidTime        time.Time `json:"valid_time"`
	Unit             string    `json:"unit"`
	CoverageRatio    float64   `json:"coverage_ratio"`
	ValidCellCount   int64     `json:"valid_cell_count"`
	MissingCellCount int64     `json:"missing_cell_count"`
}

type Layer struct {
	LayerID      string        `json:"layer_id"`
	ProductType  string        `json:"product_type"`
	VariableName string        `json:"variable_name"`
	ThresholdMMH *float64      `json:"threshold_mm_h"`
	Quantile     *float64      `json:"quantile"`
	Unit         string        `json:"unit"`
	ValidTimes   []time.Time   `json:"valid_times"`
	Legend       []LegendEntry `json:"legend"`
	Assets       []Asset       `json:"assets"`
}

type Bundle struct {
	ContractName         string         `json:"contract_name"`
	ContractVersion      string         `json:"contract_version"`
	BundleID             uuid.UUID      `json:"bundle_id"`
	RunID                uuid.UUID      `json:"run_id"`
	JobID                uuid.UUID      `json:"job_id"`
	IssueTime            time.Time      `json:"issue_time"`
	GridID               string         `json:"grid_id"`
	GridConfigVersion    string         `json:"grid_config_version"`
	PixelEdgeBounds      []float64      `json:"pixel_edge_bounds"`
	Width                int            `json:"width"`
	Height               int            `json:"height"`
	SourceForecast       SourceForecast `json:"source_forecast"`
	ModelID              string         `json:"model_id"`
	ModelVersion         string         `json:"model_version"`
	ModelConfigVersion   string         `json:"model_config_version"`
	ProductConfigVersion string         `json:"product_config_version"`
	MemberCount          int            `json:"member_count"`
	CalibrationStatus    string         `json:"calibration_status"`
	OperationalEligible  bool           `json:"operational_eligible"`
	OperationalGate      string         `json:"operational_gate"`
	Layers               []Layer        `json:"layers"`
	CreatedAt            time.Time      `json:"created_at"`
}

type AssetContent struct {
	Data      []byte
	MediaType string
	SHA256    string
	FileName  string
}

type FileStore struct {
	root string
}

func NewFileStore(root string) *FileStore {
	return &FileStore{root: filepath.Clean(root)}
}

func (store *FileStore) GetLatest(ctx context.Context) (Bundle, error) {
	bundles, err := store.ListCycles(ctx)
	if err != nil {
		return Bundle{}, err
	}
	if len(bundles) == 0 {
		return Bundle{}, ErrNotFound
	}
	return bundles[0], nil
}

func (store *FileStore) ListCycles(ctx context.Context) ([]Bundle, error) {
	return store.listBundles(ctx)
}

func (store *FileStore) GetByCycle(
	ctx context.Context,
	issueTime time.Time,
	gridID string,
) (Bundle, error) {
	if issueTime.IsZero() || strings.TrimSpace(gridID) == "" {
		return Bundle{}, ErrNotFound
	}
	bundles, err := store.ListCycles(ctx)
	if err != nil {
		return Bundle{}, err
	}
	for _, bundle := range bundles {
		if bundle.IssueTime.Equal(issueTime) && bundle.GridID == gridID {
			return bundle, nil
		}
	}
	return Bundle{}, ErrNotFound
}

func (store *FileStore) listBundles(ctx context.Context) ([]Bundle, error) {
	entries, err := os.ReadDir(store.root)
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("list ensemble-product root: %w", err)
	}
	bundles := make([]Bundle, 0, len(entries))
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !entry.IsDir() || !validUUID(entry.Name()) {
			continue
		}
		bundle, err := store.readBundle(entry.Name())
		if errors.Is(err, ErrNotFound) {
			continue
		}
		if err != nil {
			return nil, err
		}
		bundles = append(bundles, bundle)
	}
	sort.Slice(bundles, func(left, right int) bool {
		return bundles[left].CreatedAt.After(bundles[right].CreatedAt)
	})
	return bundles, nil
}

func (store *FileStore) ReadAsset(
	ctx context.Context,
	bundleID string,
	assetID string,
) (AssetContent, error) {
	if err := ctx.Err(); err != nil {
		return AssetContent{}, err
	}
	if !validUUID(bundleID) || !validAssetID(assetID) {
		return AssetContent{}, ErrNotFound
	}
	bundle, err := store.readBundle(bundleID)
	if err != nil {
		return AssetContent{}, err
	}
	var selected *Asset
	for layerIndex := range bundle.Layers {
		for assetIndex := range bundle.Layers[layerIndex].Assets {
			asset := &bundle.Layers[layerIndex].Assets[assetIndex]
			if asset.AssetID == assetID {
				selected = asset
				break
			}
		}
	}
	if selected == nil {
		return AssetContent{}, ErrNotFound
	}
	path, err := safeAssetPath(store.root, bundleID, selected.ObjectPath)
	if err != nil {
		return AssetContent{}, err
	}
	info, err := os.Stat(path)
	if err != nil {
		return AssetContent{}, fileError(err)
	}
	if !info.Mode().IsRegular() || info.Size() != selected.SizeBytes ||
		info.Size() < 4 || info.Size() > maximumAssetBytes {
		return AssetContent{}, fmt.Errorf("%w: ensemble asset size differs", ErrInvalidBundle)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return AssetContent{}, fileError(err)
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(data))
	if digest != selected.SHA256 || !validSignature(selected.MediaType, data) {
		return AssetContent{}, fmt.Errorf("%w: ensemble asset integrity differs", ErrInvalidBundle)
	}
	return AssetContent{
		Data: data, MediaType: selected.MediaType, SHA256: digest,
		FileName: filepath.Base(selected.ObjectPath),
	}, nil
}

func (store *FileStore) readBundle(bundleID string) (Bundle, error) {
	if !validUUID(bundleID) {
		return Bundle{}, ErrNotFound
	}
	manifestPath := filepath.Join(store.root, bundleID, "manifest.json")
	info, err := os.Stat(manifestPath)
	if err != nil {
		return Bundle{}, fileError(err)
	}
	if !info.Mode().IsRegular() || info.Size() < 2 || info.Size() > maximumManifestBytes {
		return Bundle{}, fmt.Errorf("%w: ensemble manifest size is invalid", ErrInvalidBundle)
	}
	data, err := os.ReadFile(manifestPath)
	if err != nil {
		return Bundle{}, fileError(err)
	}
	var bundle Bundle
	if err := json.Unmarshal(data, &bundle); err != nil {
		return Bundle{}, fmt.Errorf("%w: decode ensemble manifest", ErrInvalidBundle)
	}
	if err := validateBundle(bundle, bundleID); err != nil {
		return Bundle{}, err
	}
	return bundle, nil
}

func validateBundle(bundle Bundle, directoryID string) error {
	if bundle.ContractName != "rainpulse.ensemble-application-product-bundle" ||
		bundle.ContractVersion != "1.0" || bundle.BundleID.String() != directoryID ||
		bundle.RunID != bundle.BundleID || bundle.JobID == uuid.Nil || bundle.IssueTime.IsZero() ||
		bundle.GridID == "" || len(bundle.PixelEdgeBounds) != 4 || bundle.Width < 1 ||
		bundle.Height < 1 || bundle.MemberCount < 2 || bundle.OperationalEligible ||
		bundle.CalibrationStatus != "raw_ensemble_relative_frequency_uncalibrated" ||
		bundle.OperationalGate != "independent_fujian_probabilistic_acceptance_required" ||
		bundle.SourceForecast.URI == "" || !validSHA(bundle.SourceForecast.SHA256) ||
		bundle.SourceForecast.ContractVersion != "1.2" || len(bundle.Layers) != 8 ||
		bundle.CreatedAt.IsZero() {
		return fmt.Errorf("%w: ensemble manifest identity differs", ErrInvalidBundle)
	}
	layerIDs := make(map[string]bool, 8)
	assetIDs := make(map[string]bool, 384)
	objectPaths := make(map[string]bool, 384)
	expectedThresholds := map[string]float64{
		"probability-gt-1": 1, "probability-gt-5": 5, "probability-gt-10": 10,
		"probability-gt-20": 20, "probability-gt-50": 50,
	}
	expectedQuantiles := map[string]float64{
		"quantile-p10": 0.1, "quantile-p50": 0.5, "quantile-p90": 0.9,
	}
	cellCount := int64(bundle.Width) * int64(bundle.Height)
	for _, layer := range bundle.Layers {
		if layerIDs[layer.LayerID] || len(layer.ValidTimes) != 24 || len(layer.Assets) != 48 ||
			len(layer.Legend) < 2 || layer.VariableName == "" || layer.Unit == "" {
			return fmt.Errorf("%w: ensemble layer structure differs", ErrInvalidBundle)
		}
		layerIDs[layer.LayerID] = true
		if layer.ProductType == "probability_exceedance" {
			expected, exists := expectedThresholds[layer.LayerID]
			if !exists || layer.ThresholdMMH == nil ||
				math.Abs(*layer.ThresholdMMH-expected) > 1e-9 ||
				layer.Quantile != nil || layer.Unit != "1" {
				return fmt.Errorf("%w: probability selector differs", ErrInvalidBundle)
			}
		} else if layer.ProductType == "quantile" {
			expected, exists := expectedQuantiles[layer.LayerID]
			if !exists || layer.ThresholdMMH != nil || layer.Quantile == nil ||
				math.Abs(*layer.Quantile-expected) > 1e-9 || layer.Unit != "mm h-1" {
				return fmt.Errorf("%w: quantile selector differs", ErrInvalidBundle)
			}
		} else {
			return fmt.Errorf("%w: ensemble product type is invalid", ErrInvalidBundle)
		}
		for index, validTime := range layer.ValidTimes {
			expected := bundle.IssueTime.Add(time.Duration((index+1)*5) * time.Minute)
			if !validTime.Equal(expected) {
				return fmt.Errorf("%w: ensemble valid times differ", ErrInvalidBundle)
			}
		}
		leadFormats := make(map[string]bool, 48)
		for _, asset := range layer.Assets {
			if assetIDs[asset.AssetID] || !validAssetID(asset.AssetID) ||
				objectPaths[asset.ObjectPath] || !validObjectPath(asset.ObjectPath) ||
				!validSHA(asset.SHA256) ||
				asset.SizeBytes < 4 || asset.SizeBytes > maximumAssetBytes ||
				asset.LeadMinutes < 5 || asset.LeadMinutes > 120 ||
				asset.LeadMinutes%5 != 0 ||
				!asset.ValidTime.Equal(
					bundle.IssueTime.Add(time.Duration(asset.LeadMinutes)*time.Minute),
				) || asset.Unit != layer.Unit ||
				asset.CoverageRatio < 0 || asset.CoverageRatio > 1 ||
				asset.ValidCellCount < 0 || asset.MissingCellCount < 0 ||
				asset.ValidCellCount+asset.MissingCellCount != cellCount ||
				math.Abs(
					asset.CoverageRatio-float64(asset.ValidCellCount)/float64(cellCount),
				) > 1e-6 {
				return fmt.Errorf("%w: ensemble asset metadata differs", ErrInvalidBundle)
			}
			if (asset.AssetType == "rendered_png" && asset.MediaType != "image/png") ||
				(asset.AssetType == "application_netcdf" && asset.MediaType != "application/x-netcdf") {
				return fmt.Errorf("%w: ensemble asset format differs", ErrInvalidBundle)
			}
			formatKey := fmt.Sprintf("%03d/%s", asset.LeadMinutes, asset.AssetType)
			if leadFormats[formatKey] {
				return fmt.Errorf("%w: ensemble lead asset is duplicated", ErrInvalidBundle)
			}
			leadFormats[formatKey] = true
			assetIDs[asset.AssetID] = true
			objectPaths[asset.ObjectPath] = true
		}
		for lead := 5; lead <= 120; lead += 5 {
			for _, assetType := range []string{"rendered_png", "application_netcdf"} {
				if !leadFormats[fmt.Sprintf("%03d/%s", lead, assetType)] {
					return fmt.Errorf("%w: ensemble lead asset is missing", ErrInvalidBundle)
				}
			}
		}
	}
	expectedLayers := []string{
		"probability-gt-1", "probability-gt-5", "probability-gt-10",
		"probability-gt-20", "probability-gt-50",
		"quantile-p10", "quantile-p50", "quantile-p90",
	}
	for _, layerID := range expectedLayers {
		if !layerIDs[layerID] {
			return fmt.Errorf("%w: ensemble layer suite is incomplete", ErrInvalidBundle)
		}
	}
	return nil
}

func safeAssetPath(root, bundleID, objectPath string) (string, error) {
	if !validObjectPath(objectPath) {
		return "", fmt.Errorf("%w: ensemble object path is invalid", ErrInvalidBundle)
	}
	directory := filepath.Join(root, bundleID)
	path := filepath.Join(directory, filepath.FromSlash(objectPath))
	relative, err := filepath.Rel(directory, path)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		return "", fmt.Errorf("%w: ensemble object escapes bundle", ErrInvalidBundle)
	}
	return path, nil
}

func validUUID(value string) bool {
	parsed, err := uuid.Parse(value)
	return err == nil && parsed != uuid.Nil && parsed.String() == value
}

func validAssetID(value string) bool {
	if value == "" || len(value) > 127 {
		return false
	}
	for _, character := range value {
		if (character < 'a' || character > 'z') && (character < '0' || character > '9') &&
			character != '-' {
			return false
		}
	}
	return true
}

func validObjectPath(value string) bool {
	if value == "" || strings.Contains(value, "\\") || strings.HasPrefix(value, "/") {
		return false
	}
	cleaned := filepath.ToSlash(filepath.Clean(filepath.FromSlash(value)))
	return cleaned == value && value != "." && !strings.HasPrefix(value, "../")
}

func validSHA(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, character := range value {
		if (character < '0' || character > '9') && (character < 'a' || character > 'f') {
			return false
		}
	}
	return true
}

func validSignature(mediaType string, data []byte) bool {
	if mediaType == "image/png" {
		return len(data) >= 8 && string(data[:8]) == "\x89PNG\r\n\x1a\n"
	}
	if mediaType == "application/x-netcdf" {
		return len(data) >= 4 && (string(data[:4]) == "CDF\x01" || string(data[:4]) == "CDF\x02")
	}
	return false
}

func fileError(err error) error {
	if errors.Is(err, os.ErrNotExist) {
		return ErrNotFound
	}
	return err
}

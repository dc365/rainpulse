package nowcastnetproducts

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
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
	maximumManifestBytes = 1 << 20
	maximumAssetBytes    = 32 << 20
)

var (
	ErrNotFound      = errors.New("NowcastNet shadow product bundle not found")
	ErrInvalidBundle = errors.New("NowcastNet shadow product bundle is invalid")
)

type LegendEntry struct {
	Minimum float64 `json:"minimum"`
	Color   string  `json:"color"`
}

type ROI struct {
	YStart int `json:"y_start"`
	XStart int `json:"x_start"`
	Height int `json:"height"`
	Width  int `json:"width"`
}

type Frame struct {
	AssetID          string     `json:"asset_id"`
	ObjectPath       string     `json:"object_path"`
	MediaType        string     `json:"media_type"`
	SHA256           string     `json:"sha256"`
	SizeBytes        int64      `json:"size_bytes"`
	LeadMinutes      int        `json:"lead_time_minutes"`
	ValidTime        time.Time  `json:"valid_time"`
	Unit             string     `json:"unit"`
	CoverageRatio    float64    `json:"coverage_ratio"`
	ValidCellCount   int64      `json:"valid_cell_count"`
	MissingCellCount int64      `json:"missing_cell_count"`
	Bounds           [4]float64 `json:"pixel_edge_bounds"`
}

type Bundle struct {
	ContractName        string        `json:"contract_name"`
	ContractVersion     string        `json:"contract_version"`
	BundleID            uuid.UUID     `json:"bundle_id"`
	IssueTime           time.Time     `json:"issue_time"`
	GridID              string        `json:"grid_id"`
	GridConfigVersion   string        `json:"grid_config_version"`
	ModelID             string        `json:"model_id"`
	ModelVersion        string        `json:"model_version"`
	ProfileVersion      string        `json:"profile_version"`
	MemberCount         int           `json:"member_count"`
	CadenceMinutes      int           `json:"cadence_minutes"`
	Lifecycle           string        `json:"lifecycle"`
	OperationalEligible bool          `json:"operational_eligible"`
	ROI                 ROI           `json:"roi"`
	LegendUnit          string        `json:"legend_unit"`
	Legend              []LegendEntry `json:"legend"`
	Frames              []Frame       `json:"frames"`
	CreatedAt           time.Time     `json:"created_at"`
}

type AssetContent struct {
	Data      []byte
	MediaType string
	SHA256    string
}

type FileStore struct {
	root string
}

func NewFileStore(root string) *FileStore {
	return &FileStore{root: filepath.Clean(root)}
}

func (store *FileStore) ListCycles(ctx context.Context) ([]Bundle, error) {
	entries, err := os.ReadDir(store.root)
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("list NowcastNet product root: %w", err)
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

func (store *FileStore) GetByCycle(ctx context.Context, issueTime time.Time, gridID string) (Bundle, error) {
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

func (store *FileStore) ReadAsset(ctx context.Context, bundleID, assetID string) (AssetContent, error) {
	if err := ctx.Err(); err != nil {
		return AssetContent{}, err
	}
	if !validUUID(bundleID) || !validSegment(assetID) {
		return AssetContent{}, ErrNotFound
	}
	bundle, err := store.readBundle(bundleID)
	if err != nil {
		return AssetContent{}, err
	}
	var selected *Frame
	for index := range bundle.Frames {
		if bundle.Frames[index].AssetID == assetID {
			selected = &bundle.Frames[index]
			break
		}
	}
	if selected == nil {
		return AssetContent{}, ErrNotFound
	}
	path, err := safeAssetPath(store.root, bundleID, selected.ObjectPath)
	if err != nil {
		return AssetContent{}, err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return AssetContent{}, fileError(err)
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(data))
	if int64(len(data)) != selected.SizeBytes || digest != selected.SHA256 ||
		!validPNG(data, bundle.ROI.Width, bundle.ROI.Height) {
		return AssetContent{}, fmt.Errorf("%w: asset integrity differs", ErrInvalidBundle)
	}
	return AssetContent{Data: data, MediaType: selected.MediaType, SHA256: digest}, nil
}

func (store *FileStore) readBundle(bundleID string) (Bundle, error) {
	if !validUUID(bundleID) {
		return Bundle{}, ErrNotFound
	}
	path := filepath.Join(store.root, bundleID, "manifest.json")
	info, err := os.Stat(path)
	if err != nil {
		return Bundle{}, fileError(err)
	}
	if !info.Mode().IsRegular() || info.Size() < 2 || info.Size() > maximumManifestBytes {
		return Bundle{}, fmt.Errorf("%w: manifest size differs", ErrInvalidBundle)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return Bundle{}, fileError(err)
	}
	var bundle Bundle
	if err := json.Unmarshal(data, &bundle); err != nil {
		return Bundle{}, fmt.Errorf("%w: decode manifest", ErrInvalidBundle)
	}
	if err := validateBundle(bundle, bundleID); err != nil {
		return Bundle{}, err
	}
	return bundle, nil
}

func validateBundle(bundle Bundle, directoryID string) error {
	if bundle.ContractName != "rainpulse.nowcastnet-shadow-product-bundle" ||
		bundle.ContractVersion != "1.0" || bundle.BundleID.String() != directoryID ||
		bundle.IssueTime.IsZero() || bundle.GridID == "" || bundle.ModelID != "nowcastnet" ||
		bundle.ModelVersion == "" || bundle.ProfileVersion == "" || bundle.MemberCount != 4 ||
		bundle.CadenceMinutes != 10 || bundle.Lifecycle != "shadow" || bundle.OperationalEligible ||
		bundle.ROI.Width < 32 || bundle.ROI.Height < 32 || bundle.ROI.Width%32 != 0 || bundle.ROI.Height%32 != 0 ||
		bundle.LegendUnit != "mm/h" || len(bundle.Legend) < 2 || len(bundle.Frames) != 12 || bundle.CreatedAt.IsZero() {
		return fmt.Errorf("%w: manifest identity differs", ErrInvalidBundle)
	}
	assets := make(map[string]bool, len(bundle.Frames))
	paths := make(map[string]bool, len(bundle.Frames))
	cellCount := int64(bundle.ROI.Width) * int64(bundle.ROI.Height)
	for index, frame := range bundle.Frames {
		expectedLead := (index + 1) * 10
		if frame.AssetID == "" || !validSegment(frame.AssetID) || assets[frame.AssetID] ||
			!validObjectPath(frame.ObjectPath) || paths[frame.ObjectPath] || frame.MediaType != "image/png" ||
			!validSHA(frame.SHA256) || frame.SizeBytes < 8 || frame.SizeBytes > maximumAssetBytes ||
			frame.LeadMinutes != expectedLead || !frame.ValidTime.Equal(bundle.IssueTime.Add(time.Duration(expectedLead)*time.Minute)) ||
			frame.Unit != bundle.LegendUnit || frame.ValidCellCount != cellCount || frame.MissingCellCount != 0 ||
			math.Abs(frame.CoverageRatio-1) > 1e-9 || !validBounds(frame.Bounds) {
			return fmt.Errorf("%w: frame metadata differs", ErrInvalidBundle)
		}
		assets[frame.AssetID] = true
		paths[frame.ObjectPath] = true
	}
	return nil
}

func safeAssetPath(root, bundleID, objectPath string) (string, error) {
	if !validObjectPath(objectPath) {
		return "", fmt.Errorf("%w: asset path is invalid", ErrInvalidBundle)
	}
	directory := filepath.Join(root, bundleID)
	path := filepath.Join(directory, filepath.FromSlash(objectPath))
	relative, err := filepath.Rel(directory, path)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		return "", fmt.Errorf("%w: asset escapes bundle", ErrInvalidBundle)
	}
	return path, nil
}

func validUUID(value string) bool {
	parsed, err := uuid.Parse(value)
	return err == nil && parsed != uuid.Nil && parsed.String() == value
}

func validSegment(value string) bool {
	if value == "" || len(value) > 127 {
		return false
	}
	for _, character := range value {
		if (character < 'a' || character > 'z') && (character < '0' || character > '9') && character != '-' {
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

func validBounds(bounds [4]float64) bool {
	return bounds[0] < bounds[2] && bounds[1] < bounds[3]
}

func validPNG(data []byte, width, height int) bool {
	return len(data) >= 24 && string(data[:8]) == "\x89PNG\r\n\x1a\n" &&
		int(binary.BigEndian.Uint32(data[16:20])) == width && int(binary.BigEndian.Uint32(data[20:24])) == height
}

func fileError(err error) error {
	if errors.Is(err, os.ErrNotExist) {
		return ErrNotFound
	}
	return err
}

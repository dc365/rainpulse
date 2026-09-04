package workspace

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workspacepoint"
	"github.com/google/uuid"
)

const (
	qpePointQueryID     = "grid-rate-qpe"
	stepsPointQueryID   = "quantile-p50"
	nowcastPointQueryID = "ensemble-mean"
	pointIndexMediaType = "application/vnd.rainpulse.point-index"
)

var (
	diagnosticAssetPattern = regexp.MustCompile(`^/api/v1/diagnostics/([0-9a-f-]{36})/layers/([a-z0-9-]+)$`)
	productAssetPattern    = regexp.MustCompile(`^/api/v1/products/([0-9a-f-]{36})/assets/([0-9a-f-]{36})$`)
	ensembleAssetPattern   = regexp.MustCompile(`^/api/v1/ensemble-products/([0-9a-f-]{36})/assets/([a-z0-9-]+)$`)
	nowcastNetAssetPattern = regexp.MustCompile(`^/api/v1/workspace/nowcastnet-products/([0-9a-f-]{36})/assets/([a-z0-9-]+)$`)
	leadPattern            = regexp.MustCompile(`(?:^|-)lead-(\d{3})(?:-|$)`)
)

var (
	errUnsupportedSample = errors.New("workspace asset has no exact point-query sidecar")
	errSampleNotFound    = errors.New("workspace point-query source was not found")
	errInvalidSample     = errors.New("workspace point-query source is invalid")
)

func (handler *runtimeHandler) sampleAsset(
	ctx context.Context,
	assetURL string,
	longitude float64,
	latitude float64,
) (ExactSample, error) {
	parsed, err := url.Parse(assetURL)
	if err != nil || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.Path == "" {
		return ExactSample{}, fmt.Errorf("%w: asset URL is invalid", errInvalidSample)
	}
	if match := diagnosticAssetPattern.FindStringSubmatch(parsed.Path); match != nil {
		if match[2] != "grid-rate-qpe" {
			return ExactSample{}, errUnsupportedSample
		}
		jobID, _ := uuid.Parse(match[1])
		return handler.sampleDiagnostic(ctx, assetURL, jobID, longitude, latitude)
	}
	if match := productAssetPattern.FindStringSubmatch(parsed.Path); match != nil {
		productID, _ := uuid.Parse(match[1])
		assetID, _ := uuid.Parse(match[2])
		return handler.sampleDeterministicProduct(
			ctx, assetURL, productID, assetID, longitude, latitude,
		)
	}
	if match := ensembleAssetPattern.FindStringSubmatch(parsed.Path); match != nil {
		return handler.sampleFileProduct(
			assetURL, handler.ensembleRoot, match[1], match[2], stepsPointQueryID,
			longitude, latitude, "steps-p50",
		)
	}
	if match := nowcastNetAssetPattern.FindStringSubmatch(parsed.Path); match != nil {
		return handler.sampleNowcastNetProduct(
			ctx, assetURL, match[1], match[2], longitude, latitude,
		)
	}
	return ExactSample{}, errUnsupportedSample
}

func (handler *runtimeHandler) sampleNowcastNetProduct(
	ctx context.Context,
	assetURL string,
	bundleID string,
	assetID string,
	longitude float64,
	latitude float64,
) (ExactSample, error) {
	if handler.nowcastNetProducts == nil {
		return ExactSample{}, errSampleNotFound
	}
	if _, err := uuid.Parse(bundleID); err != nil || filepath.Base(bundleID) != bundleID {
		return ExactSample{}, fmt.Errorf("%w: bundle identity is invalid", errInvalidSample)
	}
	manifestData, _, err := handler.nowcastNetProducts.ReadObject(ctx, bundleID, "manifest.json")
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: read NowcastNet manifest: %v", errSampleNotFound, err)
	}
	metadata, err := pointQueryFromManifest(manifestData, nowcastPointQueryID)
	if err != nil {
		return ExactSample{}, err
	}
	leadMinutes, ok := leadFromAssetID(assetID)
	if !ok {
		return ExactSample{}, fmt.Errorf("%w: asset lead cannot be determined", errInvalidSample)
	}
	leadIndex := indexOfInt(metadata.LeadMinutes, leadMinutes)
	if leadIndex < 0 {
		return ExactSample{}, fmt.Errorf("%w: asset lead is absent from point query", errInvalidSample)
	}
	data, _, err := handler.nowcastNetProducts.ReadObject(ctx, bundleID, metadata.ObjectPath)
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: read NowcastNet point query: %v", errSampleNotFound, err)
	}
	return samplePointBytes(assetURL, data, metadata, longitude, latitude, leadIndex, "nowcastnet")
}

func (handler *runtimeHandler) sampleDiagnostic(
	ctx context.Context,
	assetURL string,
	jobID uuid.UUID,
	longitude float64,
	latitude float64,
) (ExactSample, error) {
	if handler.store == nil || handler.objects == nil {
		return ExactSample{}, errSampleNotFound
	}
	diagnostics, err := handler.store.GetAnalysisDiagnosticsByJob(ctx, jobID)
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: %v", errSampleNotFound, err)
	}
	manifestData, _, err := handler.objects.Read(ctx, diagnostics.BundleURI, "manifest.json")
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: read diagnostic manifest: %v", errSampleNotFound, err)
	}
	metadata, err := pointQueryFromManifest(manifestData, qpePointQueryID)
	if err != nil {
		return ExactSample{}, err
	}
	data, _, err := handler.objects.Read(ctx, diagnostics.BundleURI, metadata.ObjectPath)
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: read QPE point index: %v", errSampleNotFound, err)
	}
	return samplePointBytes(
		assetURL, data, metadata, longitude, latitude, 0, "radar-analysis",
	)
}

func (handler *runtimeHandler) sampleDeterministicProduct(
	ctx context.Context,
	assetURL string,
	productID uuid.UUID,
	assetID uuid.UUID,
	longitude float64,
	latitude float64,
) (ExactSample, error) {
	if handler.store == nil || handler.objects == nil {
		return ExactSample{}, errSampleNotFound
	}
	product, err := handler.store.GetProduct(ctx, productID)
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: product: %v", errSampleNotFound, err)
	}
	selected, err := handler.store.GetProductAsset(ctx, productID, assetID)
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: product asset: %v", errSampleNotFound, err)
	}
	assets, err := handler.store.ListProductAssets(ctx, productID)
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: product assets: %v", errSampleNotFound, err)
	}
	var indexAsset workflow.ProductAsset
	for _, candidate := range assets {
		if candidate.AssetType == "point_query_index" && candidate.MediaType == pointIndexMediaType {
			indexAsset = candidate
			break
		}
	}
	if indexAsset.ID == uuid.Nil {
		return ExactSample{}, errUnsupportedSample
	}
	headerBytes, totalSize, _, err := handler.objects.ReadRange(
		ctx, indexAsset.ObjectURI, 0, workspacepoint.HeaderBytes,
	)
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: read product point header: %v", errSampleNotFound, err)
	}
	header, err := workspacepoint.ParseHeader(headerBytes)
	if err != nil || totalSize != indexAsset.SizeBytes || totalSize != header.ExpectedSize() {
		return ExactSample{}, fmt.Errorf("%w: product point-index header differs", errInvalidSample)
	}
	leadIndex, leadMinutes, validTime, err := productFrameIndex(product, selected, header.LeadCount)
	if err != nil {
		return ExactSample{}, err
	}
	row, column, gridLongitude, gridLatitude, err := header.Point(longitude, latitude)
	if err != nil {
		return ExactSample{}, err
	}
	offset, _ := header.CellOffset(row, column)
	cell, objectSize, _, err := handler.objects.ReadRange(
		ctx, indexAsset.ObjectURI, offset, header.CellBytes(),
	)
	if err != nil || objectSize != header.ExpectedSize() {
		return ExactSample{}, fmt.Errorf("%w: read product point cell: %v", errInvalidSample, err)
	}
	values, err := header.DecodeCell(cell)
	if err != nil || leadIndex < 0 || leadIndex >= len(values) {
		return ExactSample{}, fmt.Errorf("%w: product point values differ", errInvalidSample)
	}
	value := values[leadIndex]
	return ExactSample{
		SchemaVersion: "1.0", AssetURL: assetURL,
		Longitude: longitude, Latitude: latitude,
		GridLongitude: gridLongitude, GridLatitude: gridLatitude,
		Value: value.RainRate, Confidence: value.Confidence, Valid: value.Valid,
		Unit: "mm/h", LeadTimeMinutes: leadMinutes,
		ValidTime: validTime, FrameKind: "native", Source: "product-point-index",
	}, nil
}

func productFrameIndex(
	product workflow.Product,
	asset workflow.ProductAsset,
	leadCount int,
) (int, int, string, error) {
	if len(product.ValidTimes) != leadCount {
		return 0, 0, "", fmt.Errorf("%w: product valid times differ from point index", errInvalidSample)
	}
	for index, validTime := range product.ValidTimes {
		if asset.ValidTime != nil && validTime.Equal(asset.ValidTime.UTC()) {
			lead := int(validTime.Sub(product.IssueTime.UTC()) / time.Minute)
			return index, lead, validTime.UTC().Format(time.RFC3339), nil
		}
		if asset.LeadMinutes != nil &&
			int(validTime.Sub(product.IssueTime.UTC())/time.Minute) == *asset.LeadMinutes {
			return index, *asset.LeadMinutes, validTime.UTC().Format(time.RFC3339), nil
		}
	}
	return 0, 0, "", fmt.Errorf("%w: selected product frame is absent from point index", errInvalidSample)
}

func (handler *runtimeHandler) sampleFileProduct(
	assetURL string,
	root string,
	bundleID string,
	assetID string,
	queryID string,
	longitude float64,
	latitude float64,
	source string,
) (ExactSample, error) {
	if strings.TrimSpace(root) == "" {
		return ExactSample{}, errSampleNotFound
	}
	if _, err := uuid.Parse(bundleID); err != nil || filepath.Base(bundleID) != bundleID {
		return ExactSample{}, fmt.Errorf("%w: bundle identity is invalid", errInvalidSample)
	}
	directory := filepath.Join(filepath.Clean(root), bundleID)
	manifestPath := filepath.Join(directory, "manifest.json")
	manifestData, err := os.ReadFile(manifestPath)
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: read product manifest: %v", errSampleNotFound, err)
	}
	metadata, err := pointQueryFromManifest(manifestData, queryID)
	if err != nil {
		return ExactSample{}, err
	}
	leadMinutes, ok := leadFromAssetID(assetID)
	if !ok {
		return ExactSample{}, fmt.Errorf("%w: asset lead cannot be determined", errInvalidSample)
	}
	leadIndex := indexOfInt(metadata.LeadMinutes, leadMinutes)
	if leadIndex < 0 {
		return ExactSample{}, fmt.Errorf("%w: asset lead is absent from point query", errInvalidSample)
	}
	path, err := safeLocalPointPath(directory, metadata.ObjectPath)
	if err != nil {
		return ExactSample{}, err
	}
	return samplePointFile(
		assetURL, path, metadata, longitude, latitude, leadIndex, source,
	)
}

func pointQueryFromManifest(data []byte, queryID string) (pointQueryMetadata, error) {
	var manifest fileProductManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return pointQueryMetadata{}, fmt.Errorf("%w: manifest JSON: %v", errInvalidSample, err)
	}
	metadata, exists := manifest.PointQueries[queryID]
	if !exists {
		return pointQueryMetadata{}, errUnsupportedSample
	}
	if metadata.ObjectPath == "" || metadata.SizeBytes < workspacepoint.HeaderBytes ||
		len(metadata.SHA256) != 64 || len(metadata.LeadMinutes) < 1 ||
		len(metadata.ValidTimes) != len(metadata.LeadMinutes) ||
		(len(metadata.FrameKinds) != 0 && len(metadata.FrameKinds) != len(metadata.LeadMinutes)) ||
		(len(metadata.Derivations) != 0 && len(metadata.Derivations) != len(metadata.LeadMinutes)) {
		return pointQueryMetadata{}, fmt.Errorf("%w: point-query metadata differs", errInvalidSample)
	}
	return metadata, nil
}

func samplePointBytes(
	assetURL string,
	data []byte,
	metadata pointQueryMetadata,
	longitude float64,
	latitude float64,
	leadIndex int,
	source string,
) (ExactSample, error) {
	if int64(len(data)) != metadata.SizeBytes ||
		fmt.Sprintf("%x", sha256.Sum256(data)) != metadata.SHA256 {
		return ExactSample{}, fmt.Errorf("%w: point-query checksum differs", errInvalidSample)
	}
	header, err := workspacepoint.ParseHeader(data[:workspacepoint.HeaderBytes])
	if err != nil || header.ExpectedSize() != int64(len(data)) {
		return ExactSample{}, fmt.Errorf("%w: point-query dimensions differ", errInvalidSample)
	}
	row, column, gridLongitude, gridLatitude, err := header.Point(longitude, latitude)
	if err != nil {
		return ExactSample{}, err
	}
	offset, _ := header.CellOffset(row, column)
	values, err := header.DecodeCell(data[offset : offset+header.CellBytes()])
	if err != nil || leadIndex < 0 || leadIndex >= len(values) {
		return ExactSample{}, fmt.Errorf("%w: point-query values differ", errInvalidSample)
	}
	return exactSampleFromValue(
		assetURL, metadata, values[leadIndex], longitude, latitude,
		gridLongitude, gridLatitude, leadIndex, source,
	), nil
}

func samplePointFile(
	assetURL string,
	path string,
	metadata pointQueryMetadata,
	longitude float64,
	latitude float64,
	leadIndex int,
	source string,
) (ExactSample, error) {
	file, err := os.Open(path)
	if err != nil {
		return ExactSample{}, fmt.Errorf("%w: open point-query file: %v", errSampleNotFound, err)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() != metadata.SizeBytes {
		return ExactSample{}, fmt.Errorf("%w: point-query file size differs", errInvalidSample)
	}
	headerBytes := make([]byte, workspacepoint.HeaderBytes)
	if _, err := io.ReadFull(file, headerBytes); err != nil {
		return ExactSample{}, fmt.Errorf("%w: read point-query header: %v", errInvalidSample, err)
	}
	header, err := workspacepoint.ParseHeader(headerBytes)
	if err != nil || header.ExpectedSize() != info.Size() {
		return ExactSample{}, fmt.Errorf("%w: point-query header differs", errInvalidSample)
	}
	row, column, gridLongitude, gridLatitude, err := header.Point(longitude, latitude)
	if err != nil {
		return ExactSample{}, err
	}
	offset, _ := header.CellOffset(row, column)
	cell := make([]byte, header.CellBytes())
	if _, err := file.ReadAt(cell, offset); err != nil {
		return ExactSample{}, fmt.Errorf("%w: read point-query cell: %v", errInvalidSample, err)
	}
	values, err := header.DecodeCell(cell)
	if err != nil || leadIndex < 0 || leadIndex >= len(values) {
		return ExactSample{}, fmt.Errorf("%w: point-query values differ", errInvalidSample)
	}
	// The full-file SHA is checked during bundle publication. ReadAt keeps hover
	// sampling O(one cell); periodic retention validation still verifies the file.
	return exactSampleFromValue(
		assetURL, metadata, values[leadIndex], longitude, latitude,
		gridLongitude, gridLatitude, leadIndex, source,
	), nil
}

func exactSampleFromValue(
	assetURL string,
	metadata pointQueryMetadata,
	value workspacepoint.Value,
	longitude float64,
	latitude float64,
	gridLongitude float64,
	gridLatitude float64,
	leadIndex int,
	source string,
) ExactSample {
	frameKind := "native"
	if leadIndex < len(metadata.FrameKinds) && metadata.FrameKinds[leadIndex] != "" {
		frameKind = metadata.FrameKinds[leadIndex]
	}
	derivation := ""
	if leadIndex < len(metadata.Derivations) {
		derivation = metadata.Derivations[leadIndex]
	}
	unit := metadata.Unit
	if unit == "" {
		unit = "mm/h"
	}
	validTime := ""
	if leadIndex < len(metadata.ValidTimes) {
		validTime = metadata.ValidTimes[leadIndex]
	}
	lead := 0
	if leadIndex < len(metadata.LeadMinutes) {
		lead = metadata.LeadMinutes[leadIndex]
	}
	return ExactSample{
		SchemaVersion: "1.0", AssetURL: assetURL,
		Longitude: longitude, Latitude: latitude,
		GridLongitude: gridLongitude, GridLatitude: gridLatitude,
		Value: value.RainRate, Confidence: value.Confidence, Valid: value.Valid,
		Unit: unit, LeadTimeMinutes: lead, ValidTime: validTime,
		FrameKind: frameKind, Derivation: derivation, Source: source,
	}
}

func safeLocalPointPath(root string, relative string) (string, error) {
	clean := filepath.Clean(filepath.FromSlash(relative))
	if relative == "" || filepath.IsAbs(clean) || clean == "." ||
		strings.HasPrefix(clean, ".."+string(os.PathSeparator)) {
		return "", fmt.Errorf("%w: point-query path is unsafe", errInvalidSample)
	}
	path := filepath.Join(root, clean)
	rel, err := filepath.Rel(root, path)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		return "", fmt.Errorf("%w: point-query path escapes bundle", errInvalidSample)
	}
	return path, nil
}

func leadFromAssetID(assetID string) (int, bool) {
	match := leadPattern.FindStringSubmatch(assetID)
	if match == nil {
		return 0, false
	}
	value, err := strconv.Atoi(match[1])
	return value, err == nil
}

func indexOfInt(values []int, expected int) int {
	for index, value := range values {
		if value == expected {
			return index
		}
	}
	return -1
}

func validCoordinate(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}

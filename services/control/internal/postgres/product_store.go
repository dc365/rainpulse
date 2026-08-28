package postgres

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"path"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const applicationProductBundleMediaType = "application/vnd.rainpulse.application-product-bundle+json"

var productSHA256Pattern = regexp.MustCompile(`^[a-f0-9]{64}$`)

func (store *Store) GetProductBuildInput(
	ctx context.Context,
	runID uuid.UUID,
) (orchestration.ProductBuildInput, error) {
	var input orchestration.ProductBuildInput
	var completionMetadata json.RawMessage
	input.RunID = runID
	err := store.pool.QueryRow(ctx, `
SELECT mr.model_run_id, f.issue_time, f.grid_id, f.status,
       mr.output_uri, mr.input_asset_ids, mr.model_id, mr.model_version,
       mr.config_version, attempt.metadata
FROM forecast_runs AS f
JOIN model_runs AS mr ON mr.run_id = f.run_id AND mr.status = 'completed'
JOIN job_attempts AS attempt ON attempt.job_id = mr.job_id
WHERE f.run_id = $1 AND attempt.status = 'SUCCEEDED'
ORDER BY mr.completed_at DESC
LIMIT 1`, runID).Scan(
		&input.ModelRunID, &input.IssueTime, &input.GridID, &input.CurrentStatus,
		&input.ForecastURI, &input.InputAssetIDs, &input.ModelID,
		&input.ModelVersion, &input.ModelConfigVersion, &completionMetadata,
	)
	if err == pgx.ErrNoRows {
		return orchestration.ProductBuildInput{}, workflow.ErrNotFound
	}
	if err != nil {
		return orchestration.ProductBuildInput{}, fmt.Errorf("load product-build input: %w", err)
	}
	var metadata struct {
		Assets []orchestration.JobCompletedAsset `json:"assets"`
	}
	if err := json.Unmarshal(completionMetadata, &metadata); err != nil {
		return orchestration.ProductBuildInput{}, fmt.Errorf(
			"decode ForecastOutput completion metadata: %w", err,
		)
	}
	for _, asset := range metadata.Assets {
		if asset.AssetType == "forecast_output" && asset.URI == input.ForecastURI {
			input.ForecastSHA256 = asset.SHA256
			break
		}
	}
	if input.ForecastSHA256 == "" {
		return orchestration.ProductBuildInput{}, fmt.Errorf(
			"committed model run has no ForecastOutput SHA-256",
		)
	}
	return input, nil
}

func (store *Store) CreateProductBuildBundle(
	ctx context.Context,
	bundle workflow.ProductBuildBundle,
) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin product-build transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err = tx.Exec(ctx, `
INSERT INTO config_versions (config_version, sha256, config, description, created_at)
VALUES ($1, $2, $3, 'RP-015 application product configuration', $4)
ON CONFLICT (config_version) DO NOTHING`, bundle.Job.ConfigVersion,
		bundle.ProductConfigSHA256, bundle.ProductConfig, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert product-build configuration: %w", err)
	}
	var storedHash string
	if err = tx.QueryRow(ctx, `SELECT sha256 FROM config_versions WHERE config_version = $1`,
		bundle.Job.ConfigVersion).Scan(&storedHash); err != nil {
		return fmt.Errorf("verify product-build configuration: %w", err)
	}
	if storedHash != bundle.ProductConfigSHA256 {
		return fmt.Errorf("product config version already has a different SHA-256")
	}

	var runStatus workflow.RunStatus
	var modelStatus, forecastURI, modelID, modelVersion, modelConfig string
	var issueTime time.Time
	var gridID string
	var inputAssetIDs []uuid.UUID
	if err = tx.QueryRow(ctx, `
SELECT f.status, f.issue_time, f.grid_id, mr.status, COALESCE(mr.output_uri, ''),
       mr.input_asset_ids, mr.model_id, mr.model_version, mr.config_version
FROM forecast_runs AS f
JOIN model_runs AS mr ON mr.run_id = f.run_id
WHERE f.run_id = $1 AND mr.model_run_id = $2
FOR UPDATE OF f, mr`, bundle.Run.ID, bundle.ModelRunID).Scan(
		&runStatus, &issueTime, &gridID, &modelStatus, &forecastURI,
		&inputAssetIDs, &modelID, &modelVersion, &modelConfig,
	); err != nil {
		return fmt.Errorf("lock product-build input: %w", err)
	}
	if runStatus != workflow.RunBaselineReady || modelStatus != "completed" ||
		!issueTime.Equal(bundle.Run.IssueTime) || gridID != bundle.Run.GridID ||
		forecastURI != bundle.ForecastURI || modelID != bundle.Job.ModelID ||
		modelVersion != bundle.Job.ModelVersion || modelConfig != bundle.ModelConfigVersion ||
		!slices.Equal(inputAssetIDs, bundle.InputAssetIDs) {
		return fmt.Errorf("product build input is not the committed BASELINE_READY artifact")
	}

	if _, err = tx.Exec(ctx, `
INSERT INTO jobs (
    job_id, run_id, trace_id, job_type, model_id, model_version,
    config_version, status, max_attempts, scheduled_at, created_at, updated_at,
    request_payload
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 3, $9, $9, $9, $10)`,
		bundle.Job.ID, bundle.Job.RunID, bundle.Job.TraceID, bundle.Job.JobType,
		bundle.Job.ModelID, bundle.Job.ModelVersion, bundle.Job.ConfigVersion,
		bundle.Job.Status, bundle.Job.CreatedAt, bundle.Job.RequestPayload); err != nil {
		return fmt.Errorf("insert product-build job: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO product_build_runs (
    job_id, run_id, model_run_id, forecast_uri, forecast_sha256,
    product_config_version, bundle_contract_version, status, created_at, updated_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, 'RUNNING', $8, $8)`,
		bundle.Job.ID, bundle.Run.ID, bundle.ModelRunID, bundle.ForecastURI,
		bundle.ForecastSHA256, bundle.Job.ConfigVersion, bundle.BundleContract,
		bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert product-build run: %w", err)
	}
	if _, err = tx.Exec(ctx, `
UPDATE forecast_runs SET status = 'PRODUCT_BUILDING', updated_at = CURRENT_TIMESTAMP
WHERE run_id = $1`, bundle.Run.ID); err != nil {
		return fmt.Errorf("mark forecast run product building: %w", err)
	}
	if _, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)`,
		bundle.Outbox.ID, bundle.Outbox.AggregateID, bundle.Outbox.EventType,
		bundle.Outbox.Subject, bundle.Outbox.Payload, bundle.Job.CreatedAt); err != nil {
		return fmt.Errorf("insert product-build outbox event: %w", err)
	}
	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit product-build transaction: %w", err)
	}
	return nil
}

func applyProductBuildCompletion(
	ctx context.Context,
	tx pgx.Tx,
	event orchestration.JobCompleted,
) error {
	var bundleAsset *orchestration.JobCompletedAsset
	for index := range event.Payload.Assets {
		if event.Payload.Assets[index].AssetType == "application_product_bundle" {
			if bundleAsset != nil {
				return fmt.Errorf("%w: multiple product bundle assets", orchestration.ErrInvalidEvent)
			}
			bundleAsset = &event.Payload.Assets[index]
		}
	}
	if bundleAsset == nil || bundleAsset.MediaType != applicationProductBundleMediaType {
		return fmt.Errorf("%w: application product bundle is required", orchestration.ErrInvalidEvent)
	}
	bundleDataURI, err := artifactDataURI(*bundleAsset, event.Payload.Diagnostics)
	if err != nil {
		return fmt.Errorf("%w: %v", orchestration.ErrInvalidEvent, err)
	}
	rawManifest, ok := event.Payload.Diagnostics["product_bundle"]
	if !ok {
		return fmt.Errorf("%w: product bundle manifest is required", orchestration.ErrInvalidEvent)
	}
	var manifest workflow.ApplicationProductManifest
	if err := json.Unmarshal(rawManifest, &manifest); err != nil {
		return fmt.Errorf("%w: decode product manifest: %v", orchestration.ErrInvalidEvent, err)
	}

	var record workflow.ProductBuildRecord
	var runStatus workflow.RunStatus
	var modelStatus, modelID, modelVersion, modelConfig string
	var issueTime time.Time
	var gridID string
	var inputAssetIDs []uuid.UUID
	var requestJSON json.RawMessage
	if err := tx.QueryRow(ctx, `
SELECT build.job_id, build.run_id, build.model_run_id, build.forecast_uri,
       build.forecast_sha256, build.product_config_version,
       build.bundle_contract_version, build.status, build.created_at, build.updated_at,
       f.status, f.issue_time, f.grid_id, mr.status, mr.model_id, mr.model_version,
       mr.config_version, mr.input_asset_ids, job.request_payload
FROM product_build_runs AS build
JOIN forecast_runs AS f ON f.run_id = build.run_id
JOIN model_runs AS mr ON mr.model_run_id = build.model_run_id
JOIN jobs AS job ON job.job_id = build.job_id
WHERE build.job_id = $1 AND build.run_id = $2
FOR UPDATE OF build, f, mr`, event.JobID, event.RunID).Scan(
		&record.JobID, &record.RunID, &record.ModelRunID, &record.ForecastURI,
		&record.ForecastSHA256, &record.ProductConfigVersion,
		&record.BundleContractVersion, &record.Status, &record.CreatedAt,
		&record.UpdatedAt, &runStatus, &issueTime, &gridID, &modelStatus,
		&modelID, &modelVersion, &modelConfig, &inputAssetIDs, &requestJSON,
	); err != nil {
		return fmt.Errorf("lock product-build completion: %w", err)
	}
	if runStatus != workflow.RunProductBuilding || record.Status != "RUNNING" ||
		modelStatus != "completed" {
		return fmt.Errorf("%w: product run is not PRODUCT_BUILDING", orchestration.ErrInvalidEvent)
	}
	var requested orchestration.ProductBuildRequested
	if err := json.Unmarshal(requestJSON, &requested); err != nil {
		return fmt.Errorf("decode stored product request: %w", err)
	}
	expectedBundleURI := strings.TrimRight(requested.Payload.OutputPrefix, "/") +
		"/application-products"
	if bundleAsset.URI != expectedBundleURI || manifest.RunID != event.RunID ||
		manifest.JobID != event.JobID || manifest.ModelRunID != record.ModelRunID ||
		!manifest.IssueTime.Equal(issueTime) || manifest.GridID != gridID ||
		manifest.SourceForecast.URI != record.ForecastURI ||
		manifest.SourceForecast.SHA256 != record.ForecastSHA256 ||
		manifest.SourceForecast.ContractVersion != "1.1" ||
		manifest.ModelID != modelID || manifest.ModelVersion != modelVersion ||
		manifest.ModelConfigVersion != modelConfig ||
		manifest.ProductConfigVersion != record.ProductConfigVersion ||
		manifest.ContractVersion != record.BundleContractVersion {
		return fmt.Errorf("%w: product completion identity differs from request", orchestration.ErrInvalidEvent)
	}
	if err := validateApplicationProductManifest(manifest, requested); err != nil {
		return err
	}

	for _, product := range manifest.Products {
		metadata, err := json.Marshal(map[string]any{
			"bundle_uri":               bundleAsset.URI,
			"bundle_data_uri":          bundleDataURI,
			"source_forecast_uri":      record.ForecastURI,
			"source_forecast_sha256":   record.ForecastSHA256,
			"model_config_version":     modelConfig,
			"builder_version":          manifest.BuilderVersion,
			"renderer_version":         manifest.RendererVersion,
			"palette_version":          manifest.PaletteVersion,
			"coordinate_sha256":        manifest.CoordinateSHA256,
			"coordinate_centre_bounds": manifest.CoordinateBounds,
			"pixel_edge_bounds":        manifest.PixelEdgeBounds,
			"width":                    manifest.Width,
			"height":                   manifest.Height,
		})
		if err != nil {
			return fmt.Errorf("encode product metadata: %w", err)
		}
		if _, err = tx.Exec(ctx, `
INSERT INTO products (
    product_id, run_id, model_run_id, model_id, model_version, config_version,
    input_asset_ids, product_type, grid_id, issue_time, valid_from, valid_to,
    status, metadata, valid_times, member_count, source_forecast_uri,
    source_forecast_sha256, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
          'published', $13, $14, $15, $16, $17, $18)`,
			product.ProductID, event.RunID, record.ModelRunID, modelID, modelVersion,
			record.ProductConfigVersion, inputAssetIDs, product.ProductType, gridID,
			issueTime, product.ValidTimes[0], product.ValidTimes[len(product.ValidTimes)-1],
			metadata, product.ValidTimes, product.MemberCount, record.ForecastURI,
			record.ForecastSHA256, event.Payload.FinishedAt); err != nil {
			return fmt.Errorf("insert published product %s: %w", product.ProductType, err)
		}
		publishedAssets := make([]orchestration.ProductPublishedAsset, 0, len(product.Assets))
		for _, asset := range product.Assets {
			assetID := productAssetID(product.ProductID, asset.ObjectPath)
			objectURI := bundleDataURI + "/" + asset.ObjectPath
			assetMetadata, err := json.Marshal(asset)
			if err != nil {
				return fmt.Errorf("encode product asset metadata: %w", err)
			}
			if _, err = tx.Exec(ctx, `
INSERT INTO product_assets (
    product_asset_id, product_id, asset_type, object_uri, media_type,
    sha256, size_bytes, lead_minutes, valid_time, metadata, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)`,
				assetID, product.ProductID, asset.AssetType, objectURI, asset.MediaType,
				asset.SHA256, asset.SizeBytes, asset.LeadMinutes, asset.ValidTime, assetMetadata,
				event.Payload.FinishedAt); err != nil {
				return fmt.Errorf("insert product asset %s: %w", asset.ObjectPath, err)
			}
			publishedAssets = append(publishedAssets, orchestration.ProductPublishedAsset{
				AssetID: assetID, AssetType: asset.AssetType, URI: objectURI,
				SHA256: asset.SHA256, SizeBytes: asset.SizeBytes,
				MediaType: asset.MediaType, LeadMinute: asset.LeadMinutes,
			})
		}
		published := orchestration.ProductPublished{
			SchemaVersion: orchestration.SchemaVersion,
			EventID:       productPublishedEventID(product.ProductID),
			EventType:     orchestration.ProductPublishedEventType,
			OccurredAt:    event.Payload.FinishedAt,
			RunID:         event.RunID, JobID: event.JobID, TraceID: event.TraceID,
			Payload: orchestration.ProductPublishedPayload{
				ProductID: product.ProductID, ProductType: product.ProductType,
				ModelID: modelID, ModelVersion: modelVersion,
				Config: record.ProductConfigVersion, GridID: gridID,
				IssueTime: issueTime, ValidTimes: product.ValidTimes,
				Assets: publishedAssets,
			},
		}
		publishedJSON, err := json.Marshal(published)
		if err != nil {
			return fmt.Errorf("encode product-published event: %w", err)
		}
		if _, err = tx.Exec(ctx, `
INSERT INTO outbox_events (
    event_id, aggregate_type, aggregate_id, event_type, event_version,
    subject, payload, status, available_at, created_at
) VALUES ($1, 'job', $2, $3, 1, $4, $5, 'pending', $6, $6)
ON CONFLICT (event_id) DO NOTHING`, published.EventID, event.JobID.String(),
			published.EventType, orchestration.ProductPublishedSubject,
			publishedJSON, published.OccurredAt); err != nil {
			return fmt.Errorf("insert product-published outbox event: %w", err)
		}
	}

	if _, err := tx.Exec(ctx, `
UPDATE product_build_runs
SET status = 'SUCCEEDED', bundle_uri = $2, manifest = $3,
    measured_at = $4, updated_at = CURRENT_TIMESTAMP
WHERE job_id = $1`, event.JobID, bundleAsset.URI, rawManifest,
		event.Payload.FinishedAt); err != nil {
		return fmt.Errorf("persist product-build completion: %w", err)
	}
	if _, err := tx.Exec(ctx, `
UPDATE forecast_runs SET status = 'PUBLISHED', completed_at = $2,
    updated_at = CURRENT_TIMESTAMP WHERE run_id = $1`,
		event.RunID, event.Payload.FinishedAt); err != nil {
		return fmt.Errorf("mark forecast run published: %w", err)
	}
	return nil
}

func validateApplicationProductManifest(
	manifest workflow.ApplicationProductManifest,
	requested orchestration.ProductBuildRequested,
) error {
	if manifest.ContractName != "rainpulse.application-product-bundle" ||
		manifest.Width != 501 || manifest.Height != 201 ||
		len(manifest.CoordinateBounds) != 4 || len(manifest.PixelEdgeBounds) != 4 ||
		!slices.Equal(manifest.CoordinateBounds, []float64{118, 25, 123, 27}) ||
		!slices.Equal(manifest.PixelEdgeBounds, []float64{117.995, 24.995, 123.005, 27.005}) ||
		math.Abs(manifest.LongitudeIntervalDeg-0.01) > 1e-12 ||
		math.Abs(manifest.LatitudeIntervalDeg-0.01) > 1e-12 ||
		len(manifest.Products) != 3 {
		return fmt.Errorf("%w: invalid RP-015 product manifest grid", orchestration.ErrInvalidEvent)
	}
	expectedIDs := map[workflow.ProductType]uuid.UUID{
		workflow.ProductRainRate:        requested.Payload.ProductIDs.RainRate,
		workflow.ProductAccumulation60:  requested.Payload.ProductIDs.Accumulation60,
		workflow.ProductAccumulation120: requested.Payload.ProductIDs.Accumulation120,
	}
	seenProducts := make(map[workflow.ProductType]struct{}, 3)
	seenPaths := make(map[string]struct{}, 79)
	for _, product := range manifest.Products {
		expectedID, supported := expectedIDs[product.ProductType]
		if !supported || expectedID != product.ProductID {
			return fmt.Errorf("%w: product ID differs from request", orchestration.ErrInvalidEvent)
		}
		if _, exists := seenProducts[product.ProductType]; exists || product.MemberCount != 1 {
			return fmt.Errorf("%w: duplicate or ensemble product", orchestration.ErrInvalidEvent)
		}
		seenProducts[product.ProductType] = struct{}{}
		expectedTimes, expectedAssets := 1, 3
		if product.ProductType == workflow.ProductRainRate {
			expectedTimes, expectedAssets = 24, 73
		}
		if len(product.ValidTimes) != expectedTimes || len(product.Assets) != expectedAssets {
			return fmt.Errorf("%w: product lead or asset count differs", orchestration.ErrInvalidEvent)
		}
		for index, validTime := range product.ValidTimes {
			expectedLead := 60
			if product.ProductType == workflow.ProductRainRate {
				expectedLead = (index + 1) * 5
			} else if product.ProductType == workflow.ProductAccumulation120 {
				expectedLead = 120
			}
			if !validTime.Equal(manifest.IssueTime.Add(time.Duration(expectedLead) * time.Minute)) {
				return fmt.Errorf("%w: product valid time differs", orchestration.ErrInvalidEvent)
			}
		}
		if err := validateProductAssets(product, seenPaths); err != nil {
			return err
		}
	}
	if len(seenProducts) != 3 || len(seenPaths) != 79 {
		return fmt.Errorf("%w: incomplete RP-015 product suite", orchestration.ErrInvalidEvent)
	}
	return nil
}

func validateProductAssets(
	product workflow.ProductManifestEntry,
	seenPaths map[string]struct{},
) error {
	mediaCounts := make(map[int]map[string]int)
	pointIndexes := 0
	for _, asset := range product.Assets {
		parsed, err := url.ParseRequestURI("s3://rainpulse/" + asset.ObjectPath)
		cleaned := path.Clean(asset.ObjectPath)
		if err != nil || parsed.Path == "" || parsed.RawQuery != "" || parsed.Fragment != "" ||
			cleaned != asset.ObjectPath ||
			strings.HasPrefix(cleaned, "../") || strings.HasPrefix(cleaned, "/") ||
			asset.SizeBytes <= 0 || !productSHA256Pattern.MatchString(asset.SHA256) {
			return fmt.Errorf("%w: invalid product asset identity", orchestration.ErrInvalidEvent)
		}
		if _, exists := seenPaths[asset.ObjectPath]; exists {
			return fmt.Errorf("%w: duplicate product asset path", orchestration.ErrInvalidEvent)
		}
		seenPaths[asset.ObjectPath] = struct{}{}
		if asset.AssetType == "point_query_index" {
			if product.ProductType != workflow.ProductRainRate || asset.LeadMinutes != nil ||
				asset.ValidTime != nil || asset.MediaType != "application/vnd.rainpulse.point-index" ||
				asset.Unit != "mm h-1" || asset.SizeBytes != 12084184 {
				return fmt.Errorf("%w: invalid point-query asset", orchestration.ErrInvalidEvent)
			}
			pointIndexes++
			continue
		}
		expectedAssetType := map[string]string{
			"image/png":            "rendered_png",
			"application/x-netcdf": "application_netcdf",
			"image/tiff; application=geotiff; profile=cloud-optimized": "cloud_optimized_geotiff",
		}[asset.MediaType]
		if expectedAssetType == "" || asset.AssetType != expectedAssetType {
			return fmt.Errorf("%w: product asset type differs from media type", orchestration.ErrInvalidEvent)
		}
		expectedUnit := "mm"
		if product.ProductType == workflow.ProductRainRate {
			expectedUnit = "mm h-1"
		}
		if asset.LeadMinutes == nil || asset.ValidTime == nil || asset.CoverageRatio == nil ||
			asset.CellCount == nil || asset.ValidCellCount == nil ||
			asset.MissingCellCount == nil || asset.NoRainCellCount == nil ||
			asset.Unit != expectedUnit || *asset.CellCount != 501*201 ||
			*asset.ValidCellCount < 0 || *asset.MissingCellCount < 0 ||
			*asset.NoRainCellCount < 0 || *asset.NoRainCellCount > *asset.ValidCellCount ||
			*asset.ValidCellCount+*asset.MissingCellCount != *asset.CellCount ||
			*asset.CoverageRatio < 0 || *asset.CoverageRatio > 1 {
			return fmt.Errorf("%w: product asset lacks state metadata", orchestration.ErrInvalidEvent)
		}
		expectedCoverage := float64(*asset.ValidCellCount) / float64(*asset.CellCount)
		if math.Abs(*asset.CoverageRatio-expectedCoverage) > 1e-12 {
			return fmt.Errorf("%w: product asset coverage differs from state counts", orchestration.ErrInvalidEvent)
		}
		if !asset.ValidTime.Equal(manifestValidTime(product, *asset.LeadMinutes)) {
			return fmt.Errorf("%w: product asset time differs", orchestration.ErrInvalidEvent)
		}
		if mediaCounts[*asset.LeadMinutes] == nil {
			mediaCounts[*asset.LeadMinutes] = make(map[string]int)
		}
		mediaCounts[*asset.LeadMinutes][asset.MediaType]++
	}
	for _, counts := range mediaCounts {
		if counts["image/png"] != 1 || counts["application/x-netcdf"] != 1 ||
			counts["image/tiff; application=geotiff; profile=cloud-optimized"] != 1 {
			return fmt.Errorf("%w: product lead lacks a distribution format", orchestration.ErrInvalidEvent)
		}
	}
	if product.ProductType == workflow.ProductRainRate &&
		(len(mediaCounts) != 24 || pointIndexes != 1) {
		return fmt.Errorf("%w: rain-rate asset suite is incomplete", orchestration.ErrInvalidEvent)
	}
	if product.ProductType != workflow.ProductRainRate &&
		(len(mediaCounts) != 1 || pointIndexes != 0) {
		return fmt.Errorf("%w: accumulation asset suite is incomplete", orchestration.ErrInvalidEvent)
	}
	return nil
}

func manifestValidTime(product workflow.ProductManifestEntry, leadMinutes int) time.Time {
	if product.ProductType == workflow.ProductRainRate {
		if leadMinutes < 5 || leadMinutes > 120 || leadMinutes%5 != 0 {
			return time.Time{}
		}
		return product.ValidTimes[(leadMinutes/5)-1]
	}
	if len(product.ValidTimes) == 1 &&
		((product.ProductType == workflow.ProductAccumulation60 && leadMinutes == 60) ||
			(product.ProductType == workflow.ProductAccumulation120 && leadMinutes == 120)) {
		return product.ValidTimes[0]
	}
	return time.Time{}
}

func productAssetID(productID uuid.UUID, objectPath string) uuid.UUID {
	return uuid.NewSHA1(
		uuid.NameSpaceURL,
		[]byte("rainpulse:product-asset:"+productID.String()+":"+objectPath),
	)
}

func productPublishedEventID(productID uuid.UUID) uuid.UUID {
	return uuid.NewSHA1(
		uuid.NameSpaceURL,
		[]byte("rainpulse:product-published:"+productID.String()),
	)
}

const productSelect = `
SELECT product_id, run_id, model_run_id, product_type, model_id, model_version,
       config_version, grid_id, issue_time, valid_times, member_count,
       source_forecast_uri, source_forecast_sha256, metadata, created_at
FROM products`

func (store *Store) ListProducts(
	ctx context.Context,
	limit int,
	cursor *time.Time,
	runID *uuid.UUID,
	modelID *string,
	productType *workflow.ProductType,
) ([]workflow.Product, *time.Time, error) {
	if err := validatePageLimit(limit); err != nil {
		return nil, nil, err
	}
	var cursorValue any
	if cursor != nil {
		cursorValue = cursor.UTC()
	}
	var runValue any
	if runID != nil {
		runValue = *runID
	}
	modelValue := ""
	if modelID != nil {
		modelValue = *modelID
	}
	productValue := ""
	if productType != nil {
		productValue = string(*productType)
	}
	rows, err := store.pool.Query(ctx, productSelect+`
WHERE status = 'published'
  AND ($1::uuid IS NULL OR run_id = $1)
  AND ($2 = '' OR model_id = $2)
  AND ($3 = '' OR product_type = $3)
  AND ($4::timestamptz IS NULL OR created_at < $4)
ORDER BY created_at DESC, product_id
LIMIT $5`, runValue, modelValue, productValue, cursorValue, limit+1)
	if err != nil {
		return nil, nil, fmt.Errorf("list published products: %w", err)
	}
	defer rows.Close()
	products := make([]workflow.Product, 0, limit)
	for rows.Next() {
		product, err := scanProduct(rows)
		if err != nil {
			return nil, nil, err
		}
		products = append(products, product)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, fmt.Errorf("iterate published products: %w", err)
	}
	var next *time.Time
	if len(products) > limit {
		value := products[limit-1].CreatedAt
		next = &value
		products = products[:limit]
	}
	return products, next, nil
}

func (store *Store) GetProduct(
	ctx context.Context,
	productID uuid.UUID,
) (workflow.Product, error) {
	return scanProduct(store.pool.QueryRow(ctx, productSelect+`
WHERE product_id = $1 AND status = 'published'`, productID))
}

func (store *Store) ListProductAssets(
	ctx context.Context,
	productID uuid.UUID,
) ([]workflow.ProductAsset, error) {
	rows, err := store.pool.Query(ctx, productAssetSelect+`
WHERE product_id = $1 AND deleted_at IS NULL
ORDER BY lead_minutes NULLS LAST, asset_type, product_asset_id`, productID)
	if err != nil {
		return nil, fmt.Errorf("list product assets: %w", err)
	}
	defer rows.Close()
	assets := make([]workflow.ProductAsset, 0)
	for rows.Next() {
		asset, err := scanProductAsset(rows)
		if err != nil {
			return nil, err
		}
		assets = append(assets, asset)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate product assets: %w", err)
	}
	if len(assets) == 0 {
		if _, err := store.GetProduct(ctx, productID); err != nil {
			return nil, err
		}
	}
	return assets, nil
}

func (store *Store) GetProductAsset(
	ctx context.Context,
	productID uuid.UUID,
	assetID uuid.UUID,
) (workflow.ProductAsset, error) {
	return scanProductAsset(store.pool.QueryRow(ctx, productAssetSelect+`
WHERE product_id = $1 AND product_asset_id = $2 AND deleted_at IS NULL`,
		productID, assetID))
}

const productAssetSelect = `
SELECT product_asset_id, product_id, asset_type, object_uri, media_type,
       sha256, size_bytes, lead_minutes, valid_time, metadata, created_at
FROM product_assets`

type productRowScanner interface {
	Scan(...any) error
}

func scanProduct(row productRowScanner) (workflow.Product, error) {
	var product workflow.Product
	if err := row.Scan(
		&product.ID, &product.RunID, &product.ModelRunID, &product.ProductType,
		&product.ModelID, &product.ModelVersion, &product.ConfigVersion,
		&product.GridID, &product.IssueTime, &product.ValidTimes,
		&product.MemberCount, &product.SourceForecastURI,
		&product.SourceForecastSHA256, &product.Metadata, &product.CreatedAt,
	); err != nil {
		if err == pgx.ErrNoRows {
			return workflow.Product{}, workflow.ErrNotFound
		}
		return workflow.Product{}, fmt.Errorf("scan product: %w", err)
	}
	return product, nil
}

func scanProductAsset(row productRowScanner) (workflow.ProductAsset, error) {
	var asset workflow.ProductAsset
	if err := row.Scan(
		&asset.ID, &asset.ProductID, &asset.AssetType, &asset.ObjectURI,
		&asset.MediaType, &asset.SHA256, &asset.SizeBytes, &asset.LeadMinutes,
		&asset.ValidTime, &asset.Metadata, &asset.CreatedAt,
	); err != nil {
		if err == pgx.ErrNoRows {
			return workflow.ProductAsset{}, workflow.ErrNotFound
		}
		return workflow.ProductAsset{}, fmt.Errorf("scan product asset: %w", err)
	}
	return asset, nil
}

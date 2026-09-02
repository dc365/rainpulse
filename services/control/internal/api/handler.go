package api

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"path"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/alerting"
	apiv1 "github.com/fonwee/rainpulse-nowcast/services/control/internal/api/generated"
	ensembleproductstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/ensembleproducts"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/objectstore"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operationalissues"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operationalmetrics"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/productquery"
	verificationstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/verification"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

type RunStore interface {
	Ping(context.Context) error
	LatestRun(context.Context) (workflow.Run, error)
	GetRun(context.Context, uuid.UUID) (workflow.Run, error)
	ListRuns(context.Context, int, *time.Time, *workflow.RunStatus) ([]workflow.Run, *time.Time, error)
	ListJobs(context.Context, uuid.UUID) ([]workflow.Job, error)
}

type RunCommands interface {
	Rerun(context.Context, uuid.UUID, orchestration.RegenerationRequest) (workflow.Run, error)
}

type ObservationStore interface {
	ListRadars(context.Context) ([]workflow.Radar, error)
	ListRadarStatuses(context.Context) ([]workflow.RadarStatusSummary, error)
	GetRadar(context.Context, string) (workflow.Radar, error)
	GetRadarStatus(context.Context, string) (workflow.RadarStatusSummary, error)
	ListRadarScans(context.Context, int, *string, *workflow.RadarScanStatus) ([]workflow.RadarScan, error)
	GetRadarScan(context.Context, uuid.UUID) (workflow.RadarScan, error)
	GetRadarQCMetrics(context.Context, uuid.UUID) (workflow.RadarQCMetrics, error)
	GetRadarGridMetrics(context.Context, uuid.UUID) (workflow.RadarGridMetrics, error)
	GetAnalysisMosaicMetrics(context.Context, uuid.UUID) (workflow.AnalysisMosaicMetrics, error)
	GetAnalysisQPEMetrics(context.Context, uuid.UUID) (workflow.AnalysisQPEMetrics, error)
	GetAnalysisDiagnostics(context.Context, uuid.UUID) (workflow.AnalysisDiagnostics, error)
	GetDiagnosticLayer(context.Context, uuid.UUID, string) (string, string, error)
	ListAnalysisCycles(context.Context, int, *workflow.AnalysisStatus) ([]workflow.AnalysisCycle, error)
	GetAnalysisCycle(context.Context, uuid.UUID) (workflow.AnalysisCycle, error)
}

type DiagnosticLayerReader interface {
	Read(context.Context, string, string) ([]byte, string, error)
}

type ProductStore interface {
	ListProducts(
		context.Context,
		int,
		*time.Time,
		*uuid.UUID,
		*string,
		*workflow.ProductType,
	) ([]workflow.Product, *time.Time, error)
	GetProduct(context.Context, uuid.UUID) (workflow.Product, error)
	ListProductAssets(context.Context, uuid.UUID) ([]workflow.ProductAsset, error)
	GetProductAsset(context.Context, uuid.UUID, uuid.UUID) (workflow.ProductAsset, error)
}

type ProductObjectReader interface {
	ReadObject(context.Context, string, int64) ([]byte, string, error)
	ReadRange(context.Context, string, int64, int64) ([]byte, int64, string, error)
}

type AlgorithmVerificationStore interface {
	ListRuns(context.Context) ([]verificationstore.RunSummary, error)
	GetRun(context.Context, string, string) (verificationstore.RunDetail, error)
	ListMetrics(
		context.Context,
		string,
		string,
		verificationstore.MetricFilter,
	) ([]verificationstore.Metric, error)
	GetMapFrame(
		context.Context,
		string,
		string,
		verificationstore.MapFrameFilter,
	) (verificationstore.MapFrame, error)
	ReadMapAsset(
		context.Context,
		string,
		string,
		string,
		string,
		string,
	) (verificationstore.MapAssetContent, error)
	GetProbabilityMapFrame(
		context.Context,
		string,
		string,
		verificationstore.ProbabilityMapFrameFilter,
	) (verificationstore.ProbabilityMapFrame, error)
	ReadProbabilityMapAsset(
		context.Context,
		string,
		string,
		string,
		string,
		string,
	) (verificationstore.MapAssetContent, error)
}

type ForecastVerificationStore interface {
	GetForecastVerificationStatus(
		context.Context,
		uuid.UUID,
	) (workflow.ForecastVerificationStatus, error)
}

type EnsembleProductStore interface {
	GetLatest(context.Context) (ensembleproductstore.Bundle, error)
	ListCycles(context.Context) ([]ensembleproductstore.Bundle, error)
	GetByCycle(context.Context, time.Time, string) (ensembleproductstore.Bundle, error)
	ReadAsset(context.Context, string, string) (ensembleproductstore.AssetContent, error)
}

type Options struct {
	Version              string
	AdminToken           string
	Runs                 RunStore
	Observations         ObservationStore
	Commands             RunCommands
	DiagnosticLayers     DiagnosticLayerReader
	Products             ProductStore
	ProductObjects       ProductObjectReader
	Verification         AlgorithmVerificationStore
	ForecastVerification ForecastVerificationStore
	EnsembleProducts     EnsembleProductStore
	Metrics              operationalmetrics.Provider
	Alerts               alerting.Reader
	OperationalIssues    operationalissues.Reader
	SSEPollInterval      time.Duration
}

type server struct {
	apiv1.Unimplemented
	version              string
	runs                 RunStore
	observations         ObservationStore
	commands             RunCommands
	diagnosticLayers     DiagnosticLayerReader
	products             ProductStore
	productObjects       ProductObjectReader
	verification         AlgorithmVerificationStore
	forecastVerification ForecastVerificationStore
	ensembleProducts     EnsembleProductStore
	alerts               alerting.Reader
	operationalIssues    operationalissues.Reader
	ssePollInterval      time.Duration
}

func NewHandler(options Options) http.Handler {
	pollInterval := options.SSEPollInterval
	if pollInterval <= 0 {
		pollInterval = time.Second
	}
	handler := apiv1.HandlerWithOptions(&server{
		version:              options.Version,
		runs:                 options.Runs,
		observations:         options.Observations,
		commands:             options.Commands,
		diagnosticLayers:     options.DiagnosticLayers,
		products:             options.Products,
		productObjects:       options.ProductObjects,
		verification:         options.Verification,
		forecastVerification: options.ForecastVerification,
		ensembleProducts:     options.EnsembleProducts,
		alerts:               options.Alerts,
		operationalIssues:    options.OperationalIssues,
		ssePollInterval:      pollInterval,
	}, apiv1.ChiServerOptions{BaseURL: "/api/v1"})
	protected := protectAdminRoutes(handler, options.AdminToken)
	if options.Metrics != nil {
		metricsHandler := operationalmetrics.Handler(options.Metrics, options.Version)
		next := protected
		protected = http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
			if request.URL.Path == "/metrics" {
				metricsHandler.ServeHTTP(response, request)
				return
			}
			next.ServeHTTP(response, request)
		})
	}
	return securityHeaders(protected)
}

const (
	defaultListLimit = 50
	maximumListLimit = 200
)

func protectAdminRoutes(next http.Handler, adminToken string) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if !strings.HasPrefix(request.URL.Path, "/api/v1/admin/") {
			next.ServeHTTP(response, request)
			return
		}
		response.Header().Set("Cache-Control", "no-store")
		if adminToken == "" {
			writeError(response, http.StatusForbidden, "admin_disabled", "administrative API is disabled")
			return
		}
		const prefix = "Bearer "
		authorization := request.Header.Get("Authorization")
		if !strings.HasPrefix(authorization, prefix) ||
			subtle.ConstantTimeCompare([]byte(strings.TrimPrefix(authorization, prefix)), []byte(adminToken)) != 1 {
			response.Header().Set("WWW-Authenticate", "Bearer")
			writeError(response, http.StatusUnauthorized, "unauthorized", "administrator credentials are required")
			return
		}
		next.ServeHTTP(response, request)
	})
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.Header().Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		response.Header().Set("Referrer-Policy", "no-referrer")
		response.Header().Set("X-Content-Type-Options", "nosniff")
		response.Header().Set("X-Frame-Options", "DENY")
		next.ServeHTTP(response, request)
	})
}

func listLimit(response http.ResponseWriter, requested *int) (int, bool) {
	limit := defaultListLimit
	if requested != nil {
		limit = *requested
	}
	if limit < 1 || limit > maximumListLimit {
		writeError(response, http.StatusBadRequest, "invalid_limit", "limit must be between 1 and 200")
		return 0, false
	}
	return limit, true
}

func (service *server) GetSystemStatus(response http.ResponseWriter, request *http.Request) {
	status := apiv1.SystemStatusStatusReady
	if service.runs != nil {
		ctx, cancel := context.WithTimeout(request.Context(), 2*time.Second)
		defer cancel()
		if err := service.runs.Ping(ctx); err != nil {
			status = apiv1.SystemStatusStatusDegraded
		}
	}
	writeJSON(response, http.StatusOK, apiv1.SystemStatus{
		Service: "rainpulse-control",
		Status:  status,
		Version: service.version,
	})
}

func (service *server) GetVerificationSummary(
	response http.ResponseWriter,
	request *http.Request,
	params apiv1.GetVerificationSummaryParams,
) {
	if service.forecastVerification == nil {
		writeError(response, http.StatusServiceUnavailable, "service_unavailable", "forecast verification persistence is unavailable")
		return
	}
	status, err := service.forecastVerification.GetForecastVerificationStatus(
		request.Context(), params.RunId,
	)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	metrics := make([]apiv1.VerificationMetric, 0, 5)
	promotionEligible := false
	var truthOperationalEligible *bool
	if status.Summary != nil {
		promotionEligible = status.Summary.PromotionEligible
		truthOperationalEligible = &status.Summary.TruthOperationalEligible
		for _, item := range []struct {
			name  string
			value *float64
		}{
			{"mean_fss_lk", status.Summary.Headline.MeanFSS["lk"]},
			{"mean_fss_persistence", status.Summary.Headline.MeanFSS["persistence"]},
			{"mean_fss_translation", status.Summary.Headline.MeanFSS["translation"]},
			{"lk_minus_persistence_fss", status.Summary.Headline.LKMinusPersistenceFSS},
			{"lk_minus_translation_fss", status.Summary.Headline.LKMinusTranslationFSS},
		} {
			if item.value == nil {
				continue
			}
			threshold := float32(status.Summary.Headline.ThresholdMMH)
			metrics = append(metrics, apiv1.VerificationMetric{
				Name: item.name, Threshold: &threshold, Value: *item.value,
			})
		}
	}
	writeJSON(response, http.StatusOK, apiv1.VerificationSummary{
		RunId: params.RunId, IssueTime: status.IssueTime.UTC(),
		RunStatus:          apiv1.RunStatus(status.RunStatus),
		Status:             apiv1.VerificationSummaryStatus(status.Status),
		TruthFrameCount:    status.TruthFrameCount,
		MissingLeadMinutes: status.MissingLeadMinutes,
		ProfileVersion:     status.ProfileVersion, Metrics: metrics,
		VerifiedAt:               status.VerifiedAt,
		TruthOperationalEligible: truthOperationalEligible,
		PromotionEligible:        promotionEligible,
	})
}

func (service *server) GetAlertSnapshot(response http.ResponseWriter, request *http.Request) {
	snapshot := alerting.Snapshot{
		Status: alerting.SnapshotDegraded,
		Sources: alerting.Sources{
			Prometheus:   alerting.SourceUnavailable,
			Alertmanager: alerting.SourceUnavailable,
		},
		ObservedAt: time.Now().UTC(),
	}
	if service.alerts != nil {
		snapshot = service.alerts.Snapshot(request.Context())
	}
	items := make([]apiv1.AlertRecord, 0, len(snapshot.Items))
	for _, item := range snapshot.Items {
		items = append(items, apiv1.AlertRecord{
			ActiveAt:    item.ActiveAt,
			AlertId:     item.ID,
			Annotations: item.Annotations,
			Labels:      item.Labels,
			Name:        item.Name,
			Severity:    apiv1.AlertSeverity(item.Severity),
			State:       apiv1.AlertState(item.State),
			Summary:     item.Summary,
			Value:       item.Value,
		})
	}
	response.Header().Set("Cache-Control", "no-store")
	writeJSON(response, http.StatusOK, apiv1.AlertSnapshot{
		Status: apiv1.AlertSnapshotStatus(snapshot.Status),
		Sources: apiv1.AlertSources{
			Prometheus:   apiv1.AlertSourceAvailability(snapshot.Sources.Prometheus),
			Alertmanager: apiv1.AlertSourceAvailability(snapshot.Sources.Alertmanager),
		},
		Counts: apiv1.AlertCounts{
			Total:     snapshot.Counts.Total,
			Pending:   snapshot.Counts.Pending,
			Firing:    snapshot.Counts.Firing,
			Silenced:  snapshot.Counts.Silenced,
			Inhibited: snapshot.Counts.Inhibited,
		},
		Items:      items,
		ObservedAt: snapshot.ObservedAt,
	})
}

func (service *server) GetOperationalIssueSnapshot(
	response http.ResponseWriter,
	request *http.Request,
) {
	if service.operationalIssues == nil {
		writeServiceUnavailable(response)
		return
	}
	snapshot, err := service.operationalIssues.OperationalIssues(request.Context(), 50)
	if err != nil {
		writeError(
			response,
			http.StatusServiceUnavailable,
			"operational_evidence_unavailable",
			"operational evidence is unavailable",
		)
		return
	}
	items := make([]apiv1.OperationalIssue, 0, len(snapshot.Items))
	for _, item := range snapshot.Items {
		items = append(items, apiv1.OperationalIssue{
			AgeSeconds:   item.AgeSeconds,
			AggregateId:  item.AggregateID,
			AttemptCount: item.AttemptCount,
			CreatedAt:    item.CreatedAt.UTC(),
			ErrorCode:    item.ErrorCode,
			ErrorMessage: item.ErrorMessage,
			EventId:      parseOptionalUUID(item.EventID),
			EventType:    item.EventType,
			IssueId:      item.ID,
			JobId:        parseOptionalUUID(item.JobID),
			JobType:      item.JobType,
			Kind:         apiv1.OperationalIssueKind(item.Kind),
			RunId:        parseOptionalUUID(item.RunID),
			Status:       item.Status,
			Summary:      item.Summary,
			UpdatedAt:    item.UpdatedAt.UTC(),
		})
	}
	response.Header().Set("Cache-Control", "no-store")
	writeJSON(response, http.StatusOK, apiv1.OperationalIssueSnapshot{
		Counts: apiv1.OperationalIssueCounts{
			Total:        snapshot.Counts.Total,
			FailedJobs:   snapshot.Counts.FailedJobs,
			StuckJobs:    snapshot.Counts.StuckJobs,
			OutboxEvents: snapshot.Counts.OutboxEvents,
		},
		Items:      items,
		ObservedAt: snapshot.ObservedAt.UTC(),
	})
}

func parseOptionalUUID(value *string) *uuid.UUID {
	if value == nil {
		return nil
	}
	parsed, err := uuid.Parse(*value)
	if err != nil {
		return nil
	}
	return &parsed
}

func (service *server) ListAlgorithmVerificationRuns(
	response http.ResponseWriter,
	request *http.Request,
) {
	if service.verification == nil {
		writeJSON(response, http.StatusOK, apiv1.AlgorithmVerificationRunList{
			Items: []apiv1.AlgorithmVerificationRunSummary{},
		})
		return
	}
	runs, err := service.verification.ListRuns(request.Context())
	if err != nil {
		writeAlgorithmVerificationError(response, err)
		return
	}
	items := make([]apiv1.AlgorithmVerificationRunSummary, 0, len(runs))
	for _, run := range runs {
		items = append(items, toAPIAlgorithmVerificationRun(run))
	}
	writeJSON(response, http.StatusOK, apiv1.AlgorithmVerificationRunList{Items: items})
}

func (service *server) GetAlgorithmVerificationRun(
	response http.ResponseWriter,
	request *http.Request,
	profileVersion apiv1.VerificationProfileVersion,
	runID apiv1.VerificationRunId,
) {
	if service.verification == nil {
		writeError(response, http.StatusNotFound, "not_found", "algorithm-verification run was not found")
		return
	}
	run, err := service.verification.GetRun(request.Context(), profileVersion, runID)
	if err != nil {
		writeAlgorithmVerificationError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIAlgorithmVerificationRunDetail(run))
}

func (service *server) ListAlgorithmVerificationMetrics(
	response http.ResponseWriter,
	request *http.Request,
	profileVersion apiv1.VerificationProfileVersion,
	runID apiv1.VerificationRunId,
	params apiv1.ListAlgorithmVerificationMetricsParams,
) {
	if service.verification == nil {
		writeError(response, http.StatusNotFound, "not_found", "algorithm-verification run was not found")
		return
	}
	metrics, err := service.verification.ListMetrics(
		request.Context(),
		profileVersion,
		runID,
		verificationstore.MetricFilter{
			CaseID: params.CaseId, IssueTime: params.IssueTime,
			ThresholdMMH: params.ThresholdMmH, WindowPixels: params.WindowPixels,
		},
	)
	if err != nil {
		writeAlgorithmVerificationError(response, err)
		return
	}
	items := make([]apiv1.AlgorithmVerificationMetric, 0, len(metrics))
	for _, metric := range metrics {
		items = append(items, toAPIAlgorithmVerificationMetric(metric))
	}
	writeJSON(response, http.StatusOK, apiv1.AlgorithmVerificationMetricList{Items: items})
}

func (service *server) GetAlgorithmVerificationMapFrame(
	response http.ResponseWriter,
	request *http.Request,
	profileVersion apiv1.VerificationProfileVersion,
	runID apiv1.VerificationRunId,
	params apiv1.GetAlgorithmVerificationMapFrameParams,
) {
	if service.verification == nil {
		writeError(response, http.StatusNotFound, "not_found", "algorithm-verification map was not found")
		return
	}
	frame, err := service.verification.GetMapFrame(
		request.Context(),
		profileVersion,
		runID,
		verificationstore.MapFrameFilter{
			CaseID: params.CaseId, IssueTime: params.IssueTime, LeadMinutes: params.LeadMinutes,
		},
	)
	if err != nil {
		writeAlgorithmVerificationError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIAlgorithmVerificationMapFrame(frame))
}

func (service *server) GetAlgorithmVerificationMapAsset(
	response http.ResponseWriter,
	request *http.Request,
	profileVersion apiv1.VerificationProfileVersion,
	runID apiv1.VerificationRunId,
	caseID string,
	issueKey string,
	assetID string,
) {
	if service.verification == nil {
		writeError(response, http.StatusNotFound, "not_found", "algorithm-verification map was not found")
		return
	}
	asset, err := service.verification.ReadMapAsset(
		request.Context(), profileVersion, runID, caseID, issueKey, assetID,
	)
	if err != nil {
		writeAlgorithmVerificationError(response, err)
		return
	}
	response.Header().Set("Content-Type", "image/png")
	response.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	response.Header().Set("ETag", fmt.Sprintf("%q", asset.SHA256))
	response.Header().Set("X-Content-Type-Options", "nosniff")
	response.WriteHeader(http.StatusOK)
	_, _ = response.Write(asset.Data)
}

func (service *server) GetAlgorithmVerificationProbabilityMapFrame(
	response http.ResponseWriter,
	request *http.Request,
	profileVersion apiv1.VerificationProfileVersion,
	runID apiv1.VerificationRunId,
	params apiv1.GetAlgorithmVerificationProbabilityMapFrameParams,
) {
	if service.verification == nil {
		writeError(response, http.StatusNotFound, "not_found", "algorithm-verification probability map was not found")
		return
	}
	frame, err := service.verification.GetProbabilityMapFrame(
		request.Context(), profileVersion, runID,
		verificationstore.ProbabilityMapFrameFilter{
			CaseID: params.CaseId, IssueTime: params.IssueTime,
			LeadMinutes: params.LeadMinutes, ThresholdMMH: params.ThresholdMmH,
		},
	)
	if err != nil {
		writeAlgorithmVerificationError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIAlgorithmVerificationProbabilityMapFrame(frame))
}

func (service *server) GetAlgorithmVerificationProbabilityMapAsset(
	response http.ResponseWriter,
	request *http.Request,
	profileVersion apiv1.VerificationProfileVersion,
	runID apiv1.VerificationRunId,
	caseID string,
	issueKey string,
	assetID string,
) {
	if service.verification == nil {
		writeError(response, http.StatusNotFound, "not_found", "algorithm-verification probability map was not found")
		return
	}
	asset, err := service.verification.ReadProbabilityMapAsset(
		request.Context(), profileVersion, runID, caseID, issueKey, assetID,
	)
	if err != nil {
		writeAlgorithmVerificationError(response, err)
		return
	}
	response.Header().Set("Content-Type", "image/png")
	response.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	response.Header().Set("ETag", fmt.Sprintf("%q", asset.SHA256))
	response.Header().Set("X-Content-Type-Options", "nosniff")
	response.WriteHeader(http.StatusOK)
	_, _ = response.Write(asset.Data)
}

func (service *server) GetLatestEnsembleProductBundle(
	response http.ResponseWriter,
	request *http.Request,
) {
	if service.ensembleProducts == nil {
		writeError(response, http.StatusNotFound, "not_found", "offline ensemble product was not found")
		return
	}
	bundle, err := service.ensembleProducts.GetLatest(request.Context())
	if err != nil {
		writeEnsembleProductError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIEnsembleProductBundle(bundle))
}

func (service *server) ListEnsembleProductCycles(
	response http.ResponseWriter,
	request *http.Request,
) {
	if service.ensembleProducts == nil {
		writeJSON(response, http.StatusOK, []apiv1.EnsembleProductCycle{})
		return
	}
	bundles, err := service.ensembleProducts.ListCycles(request.Context())
	if errors.Is(err, ensembleproductstore.ErrNotFound) {
		writeJSON(response, http.StatusOK, []apiv1.EnsembleProductCycle{})
		return
	}
	if err != nil {
		writeEnsembleProductError(response, err)
		return
	}
	cycles := make([]apiv1.EnsembleProductCycle, 0, len(bundles))
	for _, bundle := range bundles {
		cycles = append(cycles, apiv1.EnsembleProductCycle{
			BundleId:    bundle.BundleID,
			IssueTime:   bundle.IssueTime,
			GridId:      bundle.GridID,
			ModelId:     bundle.ModelID,
			MemberCount: bundle.MemberCount,
			CreatedAt:   bundle.CreatedAt,
		})
	}
	writeJSON(response, http.StatusOK, cycles)
}

func (service *server) GetEnsembleProductBundleByCycle(
	response http.ResponseWriter,
	request *http.Request,
	params apiv1.GetEnsembleProductBundleByCycleParams,
) {
	if service.ensembleProducts == nil {
		writeError(response, http.StatusNotFound, "not_found", "offline ensemble product was not found")
		return
	}
	bundle, err := service.ensembleProducts.GetByCycle(
		request.Context(), params.IssueTime, params.GridId,
	)
	if err != nil {
		writeEnsembleProductError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIEnsembleProductBundle(bundle))
}

func (service *server) GetEnsembleProductAsset(
	response http.ResponseWriter,
	request *http.Request,
	bundleID uuid.UUID,
	assetID string,
) {
	if service.ensembleProducts == nil {
		writeError(response, http.StatusNotFound, "not_found", "offline ensemble asset was not found")
		return
	}
	asset, err := service.ensembleProducts.ReadAsset(
		request.Context(), bundleID.String(), assetID,
	)
	if err != nil {
		writeEnsembleProductError(response, err)
		return
	}
	response.Header().Set("Content-Type", asset.MediaType)
	response.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	response.Header().Set("ETag", fmt.Sprintf("%q", asset.SHA256))
	response.Header().Set("X-Content-Type-Options", "nosniff")
	if asset.MediaType == "application/x-netcdf" {
		response.Header().Set(
			"Content-Disposition", fmt.Sprintf("attachment; filename=%q", asset.FileName),
		)
	}
	response.WriteHeader(http.StatusOK)
	_, _ = response.Write(asset.Data)
}

func (service *server) GetLatestRun(response http.ResponseWriter, request *http.Request) {
	if service.runs == nil {
		writeServiceUnavailable(response)
		return
	}
	run, err := service.runs.LatestRun(request.Context())
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIRun(run))
}

func (service *server) GetRun(response http.ResponseWriter, request *http.Request, runID apiv1.RunId) {
	if service.runs == nil {
		writeServiceUnavailable(response)
		return
	}
	run, err := service.runs.GetRun(request.Context(), runID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIRun(run))
}

func (service *server) ListRuns(response http.ResponseWriter, request *http.Request, params apiv1.ListRunsParams) {
	if service.runs == nil {
		writeServiceUnavailable(response)
		return
	}
	limit, ok := listLimit(response, params.Limit)
	if !ok {
		return
	}
	var cursor *time.Time
	if params.Cursor != nil {
		parsed, err := time.Parse(time.RFC3339Nano, *params.Cursor)
		if err != nil {
			writeError(response, http.StatusBadRequest, "invalid_cursor", "cursor must be an RFC3339 timestamp")
			return
		}
		cursor = &parsed
	}
	var status *workflow.RunStatus
	if params.Status != nil {
		value := workflow.RunStatus(*params.Status)
		status = &value
	}
	runs, next, err := service.runs.ListRuns(request.Context(), limit, cursor, status)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	items := make([]apiv1.ForecastRun, 0, len(runs))
	for _, run := range runs {
		items = append(items, toAPIRun(run))
	}
	page := apiv1.ForecastRunPage{Items: items}
	if next != nil {
		value := next.UTC().Format(time.RFC3339Nano)
		page.NextCursor = &value
	}
	writeJSON(response, http.StatusOK, page)
}

func (service *server) ListRunJobs(response http.ResponseWriter, request *http.Request, runID apiv1.RunId) {
	if service.runs == nil {
		writeServiceUnavailable(response)
		return
	}
	if _, err := service.runs.GetRun(request.Context(), runID); err != nil {
		writeStoreError(response, err)
		return
	}
	jobs, err := service.runs.ListJobs(request.Context(), runID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	items := make([]apiv1.ForecastJob, 0, len(jobs))
	for _, job := range jobs {
		items = append(items, toAPIJob(job))
	}
	writeJSON(response, http.StatusOK, items)
}

func (service *server) RerunForecastRun(response http.ResponseWriter, request *http.Request, runID apiv1.RunId) {
	if service.commands == nil {
		writeServiceUnavailable(response)
		return
	}
	var body apiv1.RegenerationRequest
	decoder := json.NewDecoder(http.MaxBytesReader(response, request.Body, 4096))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&body); err != nil {
		writeError(response, http.StatusBadRequest, "invalid_regeneration_request", "request body must be valid regeneration JSON")
		return
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeError(response, http.StatusBadRequest, "invalid_regeneration_request", "request body must contain one regeneration object")
		return
	}
	if !body.Preset.Valid() {
		writeError(response, http.StatusBadRequest, "invalid_regeneration_preset", "regeneration preset is not supported")
		return
	}
	reason := strings.TrimSpace(body.Reason)
	if count := utf8.RuneCountInString(reason); count < 3 || count > 240 {
		writeError(response, http.StatusBadRequest, "invalid_regeneration_reason", "reason must contain 3 to 240 characters")
		return
	}
	run, err := service.commands.Rerun(request.Context(), runID, orchestration.RegenerationRequest{
		Preset: orchestration.RegenerationPreset(body.Preset),
		Reason: reason,
	})
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusAccepted, toAPIRun(run))
}

func (service *server) ListRadars(response http.ResponseWriter, request *http.Request) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	radars, err := service.observations.ListRadars(request.Context())
	if err != nil {
		writeStoreError(response, err)
		return
	}
	items := make([]apiv1.Radar, 0, len(radars))
	for _, radar := range radars {
		items = append(items, toAPIRadar(radar))
	}
	writeJSON(response, http.StatusOK, items)
}

func (service *server) GetRadar(
	response http.ResponseWriter,
	request *http.Request,
	radarID apiv1.RadarId,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	radar, err := service.observations.GetRadar(request.Context(), radarID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIRadar(radar))
}

func (service *server) GetRadarStatus(
	response http.ResponseWriter,
	request *http.Request,
	radarID apiv1.RadarId,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	status, err := service.observations.GetRadarStatus(request.Context(), radarID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIRadarStatus(status))
}

func (service *server) ListRadarStatuses(response http.ResponseWriter, request *http.Request) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	statuses, err := service.observations.ListRadarStatuses(request.Context())
	if err != nil {
		writeStoreError(response, err)
		return
	}
	items := make([]apiv1.RadarStatusSummary, 0, len(statuses))
	for _, status := range statuses {
		items = append(items, toAPIRadarStatus(status))
	}
	writeJSON(response, http.StatusOK, items)
}

func (service *server) ListRadarScans(
	response http.ResponseWriter,
	request *http.Request,
	params apiv1.ListRadarScansParams,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	limit, ok := listLimit(response, params.Limit)
	if !ok {
		return
	}
	var status *workflow.RadarScanStatus
	if params.Status != nil {
		value := workflow.RadarScanStatus(*params.Status)
		status = &value
	}
	scans, err := service.observations.ListRadarScans(
		request.Context(), limit, params.RadarId, status,
	)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	items := make([]apiv1.RadarScan, 0, len(scans))
	for _, scan := range scans {
		items = append(items, toAPIRadarScan(scan))
	}
	writeJSON(response, http.StatusOK, apiv1.RadarScanPage{Items: items})
}

func (service *server) GetRadarScan(
	response http.ResponseWriter,
	request *http.Request,
	scanID apiv1.ScanId,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	scan, err := service.observations.GetRadarScan(request.Context(), scanID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIRadarScan(scan))
}

func (service *server) GetRadarScanQCSummary(
	response http.ResponseWriter,
	request *http.Request,
	scanID apiv1.ScanId,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	metrics, err := service.observations.GetRadarQCMetrics(request.Context(), scanID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIRadarQC(metrics))
}

func (service *server) GetRadarScanGridSummary(
	response http.ResponseWriter,
	request *http.Request,
	scanID apiv1.ScanId,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	metrics, err := service.observations.GetRadarGridMetrics(request.Context(), scanID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIRadarGrid(metrics))
}

func (service *server) ListAnalysisCycles(
	response http.ResponseWriter,
	request *http.Request,
	params apiv1.ListAnalysisCyclesParams,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	limit, ok := listLimit(response, params.Limit)
	if !ok {
		return
	}
	var status *workflow.AnalysisStatus
	if params.Status != nil {
		value := workflow.AnalysisStatus(*params.Status)
		status = &value
	}
	cycles, err := service.observations.ListAnalysisCycles(request.Context(), limit, status)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	items := make([]apiv1.AnalysisCycle, 0, len(cycles))
	for _, cycle := range cycles {
		items = append(items, toAPIAnalysis(cycle))
	}
	writeJSON(response, http.StatusOK, apiv1.AnalysisCyclePage{Items: items})
}

func (service *server) GetAnalysisCycle(
	response http.ResponseWriter,
	request *http.Request,
	analysisID apiv1.AnalysisId,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	cycle, err := service.observations.GetAnalysisCycle(request.Context(), analysisID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIAnalysis(cycle))
}

func (service *server) GetAnalysisMosaicSummary(
	response http.ResponseWriter,
	request *http.Request,
	analysisID apiv1.AnalysisId,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	metrics, err := service.observations.GetAnalysisMosaicMetrics(
		request.Context(), analysisID,
	)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, metrics)
}

func (service *server) GetAnalysisQpeSummary(
	response http.ResponseWriter,
	request *http.Request,
	analysisID apiv1.AnalysisId,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	metrics, err := service.observations.GetAnalysisQPEMetrics(
		request.Context(), analysisID,
	)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, metrics)
}

func (service *server) GetAnalysisDiagnostics(
	response http.ResponseWriter,
	request *http.Request,
	analysisID apiv1.AnalysisId,
) {
	if service.observations == nil {
		writeServiceUnavailable(response)
		return
	}
	diagnostics, err := service.observations.GetAnalysisDiagnostics(
		request.Context(), analysisID,
	)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIDiagnostics(diagnostics))
}

func (service *server) GetDiagnosticLayer(
	response http.ResponseWriter,
	request *http.Request,
	jobID apiv1.JobId,
	layerID apiv1.LayerId,
) {
	if service.observations == nil || service.diagnosticLayers == nil {
		writeServiceUnavailable(response)
		return
	}
	bundleURI, objectPath, err := service.observations.GetDiagnosticLayer(
		request.Context(), jobID, layerID,
	)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	data, etag, err := service.diagnosticLayers.Read(
		request.Context(), bundleURI, objectPath,
	)
	if err != nil {
		if errors.Is(err, objectstore.ErrNotFound) {
			writeError(response, http.StatusNotFound, "not_found", "diagnostic layer was not found")
			return
		}
		writeError(
			response,
			http.StatusBadGateway,
			"object_store_error",
			"diagnostic layer could not be read",
		)
		return
	}
	if len(data) < 8 || string(data[:8]) != "\x89PNG\r\n\x1a\n" {
		writeError(
			response,
			http.StatusBadGateway,
			"invalid_diagnostic_layer",
			"diagnostic layer is not a PNG",
		)
		return
	}
	response.Header().Set("Content-Type", "image/png")
	response.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	response.Header().Set("X-Content-Type-Options", "nosniff")
	if etag != "" {
		response.Header().Set("ETag", fmt.Sprintf("%q", etag))
	}
	response.WriteHeader(http.StatusOK)
	_, _ = response.Write(data)
}

func (service *server) ListProducts(
	response http.ResponseWriter,
	request *http.Request,
	params apiv1.ListProductsParams,
) {
	if service.products == nil {
		writeServiceUnavailable(response)
		return
	}
	limit, ok := listLimit(response, params.Limit)
	if !ok {
		return
	}
	var cursor *time.Time
	if params.Cursor != nil {
		parsed, err := time.Parse(time.RFC3339Nano, *params.Cursor)
		if err != nil {
			writeError(response, http.StatusBadRequest, "invalid_cursor", "cursor must be an RFC3339 timestamp")
			return
		}
		cursor = &parsed
	}
	var productType *workflow.ProductType
	if params.ProductType != nil {
		value := workflow.ProductType(*params.ProductType)
		productType = &value
	}
	products, next, err := service.products.ListProducts(
		request.Context(), limit, cursor, params.RunId, params.ModelId, productType,
	)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	items := make([]apiv1.Product, 0, len(products))
	for _, product := range products {
		items = append(items, toAPIProduct(product))
	}
	page := apiv1.ProductPage{Items: items}
	if next != nil {
		value := next.UTC().Format(time.RFC3339Nano)
		page.NextCursor = &value
	}
	writeJSON(response, http.StatusOK, page)
}

func (service *server) GetProduct(
	response http.ResponseWriter,
	request *http.Request,
	productID apiv1.ProductId,
) {
	if service.products == nil {
		writeServiceUnavailable(response)
		return
	}
	product, err := service.products.GetProduct(request.Context(), productID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, toAPIProduct(product))
}

func (service *server) ListProductAssets(
	response http.ResponseWriter,
	request *http.Request,
	productID apiv1.ProductId,
) {
	if service.products == nil {
		writeServiceUnavailable(response)
		return
	}
	assets, err := service.products.ListProductAssets(request.Context(), productID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	items := make([]apiv1.ProductAsset, 0, len(assets))
	for _, asset := range assets {
		items = append(items, toAPIProductAsset(asset))
	}
	writeJSON(response, http.StatusOK, items)
}

func (service *server) GetProductAssetContent(
	response http.ResponseWriter,
	request *http.Request,
	productID apiv1.ProductId,
	assetID apiv1.AssetId,
) {
	if service.products == nil || service.productObjects == nil {
		writeServiceUnavailable(response)
		return
	}
	asset, err := service.products.GetProductAsset(request.Context(), productID, assetID)
	if err != nil {
		writeStoreError(response, err)
		return
	}
	data, _, err := service.productObjects.ReadObject(
		request.Context(), asset.ObjectURI, objectstore.MaximumProductAssetBytes,
	)
	if err != nil {
		writeProductObjectError(response, err)
		return
	}
	if int64(len(data)) != asset.SizeBytes || fmt.Sprintf("%x", sha256.Sum256(data)) != asset.SHA256 ||
		!validProductSignature(asset.MediaType, data) {
		writeError(
			response,
			http.StatusBadGateway,
			"invalid_product_asset",
			"registered product asset failed integrity validation",
		)
		return
	}
	etag := fmt.Sprintf("%q", asset.SHA256)
	if request.Header.Get("If-None-Match") == etag {
		response.WriteHeader(http.StatusNotModified)
		return
	}
	response.Header().Set("Content-Type", asset.MediaType)
	response.Header().Set("Content-Length", fmt.Sprintf("%d", len(data)))
	response.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	response.Header().Set("X-Content-Type-Options", "nosniff")
	response.Header().Set("ETag", etag)
	if asset.MediaType != "image/png" {
		response.Header().Set(
			"Content-Disposition",
			fmt.Sprintf("attachment; filename=%q", path.Base(asset.ObjectURI)),
		)
	}
	response.WriteHeader(http.StatusOK)
	_, _ = response.Write(data)
}

func (service *server) GetPointForecast(
	response http.ResponseWriter,
	request *http.Request,
	params apiv1.GetPointForecastParams,
) {
	product, asset, header, ok := service.loadPointIndex(
		response, request, params.ProductId,
	)
	if !ok {
		return
	}
	row, column, gridLongitude, gridLatitude, err := header.Point(
		params.Longitude, params.Latitude,
	)
	if err != nil {
		writeError(response, http.StatusBadRequest, "point_outside_grid", err.Error())
		return
	}
	offset, _ := header.CellOffset(row, column)
	data, totalSize, _, err := service.productObjects.ReadRange(
		request.Context(), asset.ObjectURI, offset, header.CellBytes(),
	)
	if err != nil {
		writeProductObjectError(response, err)
		return
	}
	if totalSize != asset.SizeBytes || totalSize != header.ExpectedSize() {
		writeError(response, http.StatusBadGateway, "invalid_point_index", "point index size differs")
		return
	}
	values, err := header.DecodeCell(data)
	if err != nil || len(product.ValidTimes) != len(values) {
		writeError(response, http.StatusBadGateway, "invalid_point_index", "point index values differ")
		return
	}
	result := make([]apiv1.PointForecastValue, len(values))
	for index, value := range values {
		result[index] = apiv1.PointForecastValue{
			ValidTime: product.ValidTimes[index].UTC(), LeadTimeMinutes: (index + 1) * 5,
			RainRate: value.RainRate, Confidence: value.Confidence, Valid: value.Valid,
		}
	}
	writeJSON(response, http.StatusOK, apiv1.PointForecast{
		ProductId: product.ID, Longitude: params.Longitude, Latitude: params.Latitude,
		GridLongitude: gridLongitude, GridLatitude: gridLatitude, Values: result,
	})
}

func (service *server) GetAreaStatistics(
	response http.ResponseWriter,
	request *http.Request,
	params apiv1.GetAreaStatisticsParams,
) {
	product, asset, header, ok := service.loadPointIndex(
		response, request, params.ProductId,
	)
	if !ok {
		return
	}
	window, err := header.BoundingBox(params.Bbox)
	if err != nil {
		writeError(response, http.StatusBadRequest, "bbox_outside_grid", err.Error())
		return
	}
	start, _ := header.CellOffset(window.RowStart, window.ColumnStart)
	end, _ := header.CellOffset(window.RowEnd, window.ColumnEnd)
	length := end - start + header.CellBytes()
	data, totalSize, _, err := service.productObjects.ReadRange(
		request.Context(), asset.ObjectURI, start, length,
	)
	if err != nil {
		writeProductObjectError(response, err)
		return
	}
	if totalSize != asset.SizeBytes || totalSize != header.ExpectedSize() {
		writeError(response, http.StatusBadGateway, "invalid_point_index", "point index size differs")
		return
	}
	width := window.ColumnEnd - window.ColumnStart + 1
	rows := make([][]byte, window.RowEnd-window.RowStart+1)
	rowStride := int64(header.Width) * header.CellBytes()
	rowLength := int64(width) * header.CellBytes()
	for index := range rows {
		offset := int64(index) * rowStride
		rows[index] = data[offset : offset+rowLength]
	}
	statistics, err := header.SummarizeRows(rows, window, params.LeadTimeMinutes)
	if err != nil {
		writeError(response, http.StatusBadRequest, "invalid_lead_time", err.Error())
		return
	}
	total := statistics.ValidCount + statistics.MissingCount
	ratio := float32(0)
	if total > 0 {
		ratio = float32(float64(statistics.ValidCount) / float64(total))
	}
	writeJSON(response, http.StatusOK, apiv1.AreaStatistics{
		ProductId: product.ID, Bbox: params.Bbox,
		ValidTime:       product.IssueTime.UTC().Add(time.Duration(params.LeadTimeMinutes) * time.Minute),
		LeadTimeMinutes: params.LeadTimeMinutes,
		ValidPixelRatio: ratio, ValidPixelCount: statistics.ValidCount,
		MissingPixelCount: statistics.MissingCount,
		MaxRainRate:       float32(statistics.Maximum), MeanRainRate: float32(statistics.Mean),
	})
}

func (service *server) loadPointIndex(
	response http.ResponseWriter,
	request *http.Request,
	productID uuid.UUID,
) (workflow.Product, workflow.ProductAsset, productquery.Header, bool) {
	if service.products == nil || service.productObjects == nil {
		writeServiceUnavailable(response)
		return workflow.Product{}, workflow.ProductAsset{}, productquery.Header{}, false
	}
	product, err := service.products.GetProduct(request.Context(), productID)
	if err != nil {
		writeStoreError(response, err)
		return workflow.Product{}, workflow.ProductAsset{}, productquery.Header{}, false
	}
	if product.ProductType != workflow.ProductRainRate {
		writeError(response, http.StatusBadRequest, "unsupported_product", "point queries require rain_rate")
		return workflow.Product{}, workflow.ProductAsset{}, productquery.Header{}, false
	}
	assets, err := service.products.ListProductAssets(request.Context(), productID)
	if err != nil {
		writeStoreError(response, err)
		return workflow.Product{}, workflow.ProductAsset{}, productquery.Header{}, false
	}
	var index workflow.ProductAsset
	for _, candidate := range assets {
		if candidate.AssetType == "point_query_index" &&
			candidate.MediaType == "application/vnd.rainpulse.point-index" {
			index = candidate
			break
		}
	}
	if index.ID == uuid.Nil {
		writeError(response, http.StatusNotFound, "not_found", "point-query index was not found")
		return workflow.Product{}, workflow.ProductAsset{}, productquery.Header{}, false
	}
	headerData, totalSize, _, err := service.productObjects.ReadRange(
		request.Context(), index.ObjectURI, 0, productquery.HeaderBytes,
	)
	if err != nil {
		writeProductObjectError(response, err)
		return workflow.Product{}, workflow.ProductAsset{}, productquery.Header{}, false
	}
	header, err := productquery.ParseHeader(headerData)
	if err != nil || totalSize != index.SizeBytes || totalSize != header.ExpectedSize() {
		writeError(response, http.StatusBadGateway, "invalid_point_index", "point index header differs")
		return workflow.Product{}, workflow.ProductAsset{}, productquery.Header{}, false
	}
	return product, index, header, true
}

func (service *server) StreamEvents(response http.ResponseWriter, request *http.Request, params apiv1.StreamEventsParams) {
	selected := 0
	for _, present := range []bool{params.RunId != nil, params.ScanId != nil, params.AnalysisId != nil} {
		if present {
			selected++
		}
	}
	if selected > 1 {
		writeError(response, http.StatusBadRequest, "invalid_stream_filter", "select only one workflow identity")
		return
	}
	requiresRuns := params.ScanId == nil && params.AnalysisId == nil
	if (requiresRuns && service.runs == nil) || (!requiresRuns && service.observations == nil) {
		writeServiceUnavailable(response)
		return
	}
	flusher, ok := response.(http.Flusher)
	if !ok {
		writeError(response, http.StatusInternalServerError, "stream_unsupported", "response streaming is unavailable")
		return
	}

	load := service.streamLoader(params)
	snapshot, err := load(request.Context())
	if err != nil {
		writeStoreError(response, err)
		return
	}

	response.Header().Set("Content-Type", "text/event-stream")
	response.Header().Set("Cache-Control", "no-cache")
	response.Header().Set("Connection", "keep-alive")
	response.Header().Set("X-Accel-Buffering", "no")
	response.WriteHeader(http.StatusOK)
	if err := writeStreamEvent(response, snapshot); err != nil {
		return
	}
	flusher.Flush()

	lastID := snapshot.ID
	lastUpdated := snapshot.UpdatedAt
	ticker := time.NewTicker(service.ssePollInterval)
	defer ticker.Stop()
	heartbeat := time.NewTicker(15 * time.Second)
	defer heartbeat.Stop()
	for {
		select {
		case <-request.Context().Done():
			return
		case <-heartbeat.C:
			_, _ = fmt.Fprint(response, ": keepalive\n\n")
			flusher.Flush()
		case <-ticker.C:
			current, err := load(request.Context())
			if err != nil {
				continue
			}
			if current.ID == lastID && !current.UpdatedAt.After(lastUpdated) {
				continue
			}
			if err := writeStreamEvent(response, current); err != nil {
				return
			}
			flusher.Flush()
			lastID = current.ID
			lastUpdated = current.UpdatedAt
		}
	}
}

type streamSnapshot struct {
	ID        string
	UpdatedAt time.Time
	EventType string
	Data      any
}

type streamLoader func(context.Context) (streamSnapshot, error)

func (service *server) streamLoader(params apiv1.StreamEventsParams) streamLoader {
	if params.ScanId != nil {
		scanID := *params.ScanId
		return func(ctx context.Context) (streamSnapshot, error) {
			scan, err := service.observations.GetRadarScan(ctx, scanID)
			return streamSnapshot{
				ID: scan.ID.String(), UpdatedAt: scan.UpdatedAt,
				EventType: "radar.scan.updated", Data: toAPIRadarScan(scan),
			}, err
		}
	}
	if params.AnalysisId != nil {
		analysisID := *params.AnalysisId
		return func(ctx context.Context) (streamSnapshot, error) {
			cycle, err := service.observations.GetAnalysisCycle(ctx, analysisID)
			return streamSnapshot{
				ID: cycle.ID.String(), UpdatedAt: cycle.UpdatedAt,
				EventType: "analysis.cycle.updated", Data: toAPIAnalysis(cycle),
			}, err
		}
	}
	load := service.runs.LatestRun
	if params.RunId != nil {
		runID := *params.RunId
		load = func(ctx context.Context) (workflow.Run, error) {
			return service.runs.GetRun(ctx, runID)
		}
	}
	return func(ctx context.Context) (streamSnapshot, error) {
		run, err := load(ctx)
		return streamSnapshot{
			ID: run.ID.String(), UpdatedAt: run.UpdatedAt,
			EventType: "run.updated", Data: toAPIRun(run),
		}, err
	}
}

func writeStreamEvent(response http.ResponseWriter, snapshot streamSnapshot) error {
	data, err := json.Marshal(snapshot.Data)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(
		response, "id: %s:%d\nevent: %s\ndata: %s\n\n",
		snapshot.ID, snapshot.UpdatedAt.UnixNano(), snapshot.EventType, data,
	)
	return err
}

func toAPIRun(run workflow.Run) apiv1.ForecastRun {
	return apiv1.ForecastRun{
		RunId:          run.ID,
		IssueTime:      run.IssueTime.UTC(),
		GridId:         run.GridID,
		ConfigVersion:  run.ConfigVersion,
		Status:         apiv1.RunStatus(run.Status),
		DegradedReason: run.DegradedReason,
		RerunOf:        run.RerunOf,
		CreatedAt:      run.CreatedAt.UTC(),
		UpdatedAt:      run.UpdatedAt.UTC(),
	}
}

func toAPIJob(job workflow.Job) apiv1.ForecastJob {
	attempt := job.Attempt
	return apiv1.ForecastJob{
		JobId:         job.ID,
		RunId:         job.RunID,
		JobType:       job.JobType,
		ModelVersion:  job.ModelVersion,
		ConfigVersion: job.ConfigVersion,
		Status:        apiv1.JobStatus(job.Status),
		Attempt:       &attempt,
		StartedAt:     utcPointer(job.StartedAt),
		FinishedAt:    utcPointer(job.FinishedAt),
		RuntimeMs:     job.RuntimeMS,
		ErrorCode:     job.ErrorCode,
		ErrorMessage:  job.ErrorMessage,
		CreatedAt:     job.CreatedAt.UTC(),
	}
}

func toAPIRadar(radar workflow.Radar) apiv1.Radar {
	return apiv1.Radar{
		RadarId: radar.ID, DisplayName: radar.DisplayName,
		Lifecycle:     apiv1.RadarLifecycle(radar.Lifecycle),
		ConfigVersion: radar.ConfigVersion,
		CreatedAt:     radar.CreatedAt.UTC(), UpdatedAt: radar.UpdatedAt.UTC(),
	}
}

func toAPIRadarStatus(status workflow.RadarStatusSummary) apiv1.RadarStatusSummary {
	var scanStatus *apiv1.RadarScanRunStatus
	if status.ScanStatus != nil {
		value := apiv1.RadarScanRunStatus(*status.ScanStatus)
		scanStatus = &value
	}
	result := apiv1.RadarStatusSummary{
		RadarId: status.RadarID, Health: apiv1.RadarHealthState(status.Health),
		DisplayName: status.DisplayName, Lifecycle: apiv1.RadarLifecycle(status.Lifecycle),
		ConfigVersion: status.ConfigVersion,
		LatestScanId:  status.LatestScanID, LatestScanTime: utcPointer(status.LatestScanTime),
		ScanStatus: scanStatus, ScanCompleteness: float32Pointer(status.ScanCompleteness),
		MeanQualityIndex:              float32Pointer(status.MeanQualityIndex),
		DataDelaySeconds:              status.DataDelaySeconds,
		ParticipatingInLatestAnalysis: status.ParticipatingInLatestAnalysis,
	}
	if status.HealthMetrics != nil {
		result.HealthMetrics = toAPIRadarHealth(*status.HealthMetrics)
	}
	if status.QCMetrics != nil {
		qc := toAPIRadarQC(*status.QCMetrics)
		result.QcMetrics = &qc
	}
	return result
}

func toAPIRadarQC(metrics workflow.RadarQCMetrics) apiv1.RadarQCMetrics {
	statuses := make(map[string]apiv1.RadarQCMetricsModuleStatuses, len(metrics.ModuleStatuses))
	for name, status := range metrics.ModuleStatuses {
		statuses[name] = apiv1.RadarQCMetricsModuleStatuses(status)
	}
	return apiv1.RadarQCMetrics{
		ScanId: metrics.ScanID, RadarId: metrics.RadarID,
		QcProfile: metrics.QCProfile, QcPipelineVersion: metrics.QCPipelineVersion,
		FlagDefinitionVersion:      metrics.FlagDefinitionVersion,
		HealthState:                apiv1.RadarHealthState(metrics.HealthState),
		MeanQualityIndex:           float32(metrics.MeanQualityIndex),
		ValidGateCount:             metrics.ValidGateCount,
		MissingGateCount:           metrics.MissingGateCount,
		LowQualityGateCount:        metrics.LowQualityGateCount,
		NoRainGateCount:            metrics.NoRainGateCount,
		RadialInterferenceRayCount: metrics.RadialInterferenceRayCount,
		GroundClutterGateCount:     metrics.GroundClutterGateCount,
		SeaClutterGateCount:        metrics.SeaClutterGateCount,
		ApGateCount:                metrics.APGateCount,
		ModuleStatuses:             statuses,
		MeasuredAt:                 metrics.MeasuredAt.UTC(),
	}
}

func toAPIRadarGrid(metrics workflow.RadarGridMetrics) apiv1.RadarGridMetrics {
	return apiv1.RadarGridMetrics{
		ScanId: metrics.ScanID, RadarId: metrics.RadarID, GridId: metrics.GridID,
		GridConfigVersion: metrics.GridConfigVersion, ProfileVersion: metrics.ProfileVersion,
		AlgorithmVersion: metrics.AlgorithmVersion, DemAssetVersion: metrics.DEMAssetVersion,
		VerticalDatumStatus: apiv1.RadarGridMetricsVerticalDatumStatus(metrics.VerticalDatumStatus),
		OperationalEligible: metrics.OperationalEligible, OperationalReasons: metrics.OperationalReasons,
		GridCellCount: metrics.GridCellCount, ValidCellCount: metrics.ValidCellCount,
		MissingCellCount: metrics.MissingCellCount, LowQualityCellCount: metrics.LowQualityCellCount,
		ValidCoverageRatio: float32(metrics.ValidCoverageRatio), MeanQualityIndex: float32(metrics.MeanQualityIndex),
		BeamBlockedMissingCellCount: metrics.BeamBlockedMissingCellCount,
		SelectionCounts:             metrics.SelectionCounts, SkippedSweeps: metrics.SkippedSweeps,
		MeasuredAt: metrics.MeasuredAt.UTC(),
	}
}

func toAPIRadarHealth(health workflow.RadarHealthMetrics) *apiv1.RadarHealthMetrics {
	fields := make([]apiv1.RadarFieldAvailability, 0, len(health.FieldAvailability))
	for _, field := range health.FieldAvailability {
		fields = append(fields, apiv1.RadarFieldAvailability{
			Field: field.Field, Available: field.Available,
			PresentSweepCount:   field.PresentSweepCount,
			FiniteGateRatio:     float32(field.FiniteGateRatio),
			OutOfRangeGateCount: field.OutOfRangeGateCount, Unit: field.Unit,
		})
	}
	missingSweeps := make([]int, len(health.MissingSweepNumbers))
	for index, value := range health.MissingSweepNumbers {
		missingSweeps[index] = int(value)
	}
	return &apiv1.RadarHealthMetrics{
		ScanId: health.ScanID, RadarId: health.RadarID,
		RadarConfigVersion:   health.RadarConfigVersion,
		HealthProfileVersion: health.HealthProfileVersion,
		Health:               apiv1.RadarHealthState(health.Health), HealthReasons: health.HealthReasons,
		ScanCompleteness:   float32(health.ScanCompleteness),
		ExpectedSweepCount: health.ExpectedSweepCount, ActualSweepCount: health.ActualSweepCount,
		MissingSweepNumbers: missingSweeps,
		ExpectedRadialCount: health.ExpectedRadialCount, ActualRadialCount: health.ActualRadialCount,
		MissingRadialCount:     health.MissingRadialCount,
		MaximumAzimuthGapDeg:   float32(health.MaximumAzimuthGapDeg),
		FieldAvailabilityRatio: float32(health.FieldAvailabilityRatio), FieldAvailability: fields,
		NoiseLevel: apiv1.RadarNoiseLevel{
			Source: health.NoiseLevel.Source, SampleCount: health.NoiseLevel.SampleCount,
			HorizontalDbm: float32ValuePointer(health.NoiseLevel.HorizontalDBM),
			VerticalDbm:   float32ValuePointer(health.NoiseLevel.VerticalDBM),
		},
		ChannelStatus:       apiv1.RadarHealthMetricsChannelStatus(health.ChannelStatus),
		OutOfRangeGateCount: health.OutOfRangeGateCount,
		OutOfRangeGateRatio: float32(health.OutOfRangeGateRatio),
		AnomalyCount:        health.AnomalyCount, LayerAnomalies: health.LayerAnomalies,
		Warnings: health.Warnings, MeasuredAt: health.MeasuredAt.UTC(),
	}
}

func toAPIRadarScan(scan workflow.RadarScan) apiv1.RadarScan {
	return apiv1.RadarScan{
		ScanId: scan.ID, RunId: scan.RunID, RadarId: scan.RadarID,
		VolumeStartTime: scan.VolumeStartTime.UTC(), VolumeEndTime: scan.VolumeEndTime.UTC(),
		RadarConfigVersion: scan.RadarConfigVersion,
		Status:             apiv1.RadarScanRunStatus(scan.Status), DegradedReason: scan.DegradedReason,
		NormalizedUri: scan.NormalizedURI, QcUri: scan.QCURI, GridUri: scan.GridURI,
		ScanCompleteness: float32Pointer(scan.ScanCompleteness),
		MeanQualityIndex: float32Pointer(scan.MeanQualityIndex),
		CreatedAt:        scan.CreatedAt.UTC(), UpdatedAt: scan.UpdatedAt.UTC(),
	}
}

func toAPIAnalysis(cycle workflow.AnalysisCycle) apiv1.AnalysisCycle {
	radars := make([]apiv1.AnalysisRadar, 0, len(cycle.Radars))
	for _, radar := range cycle.Radars {
		radars = append(radars, apiv1.AnalysisRadar{
			RadarId: radar.RadarID, ScanId: radar.ScanID,
			State:             apiv1.AnalysisRadarState(radar.State),
			TimeOffsetSeconds: radar.TimeOffsetSeconds,
			MeanQualityIndex:  float32Pointer(radar.MeanQualityIndex),
			ExclusionReason:   radar.ExclusionReason,
		})
	}
	return apiv1.AnalysisCycle{
		AnalysisId: cycle.ID, RunId: cycle.RunID, AnalysisTime: cycle.AnalysisTime.UTC(),
		GridId: cycle.GridID, ConfigVersion: cycle.ConfigVersion,
		Status:         apiv1.AnalysisCycleStatus(cycle.Status),
		DegradedReason: cycle.DegradedReason, RadarCount: cycle.RadarCount,
		ValidCoverageRatio: float32Pointer(cycle.ValidCoverageRatio),
		MeanQualityIndex:   float32Pointer(cycle.MeanQualityIndex),
		MosaicUri:          cycle.MosaicURI, AnalysisUri: cycle.AnalysisURI, Radars: radars,
		CreatedAt: cycle.CreatedAt.UTC(), UpdatedAt: cycle.UpdatedAt.UTC(),
	}
}

func toAPIDiagnostics(value workflow.AnalysisDiagnostics) apiv1.DiagnosticBundle {
	manifest := value.Manifest
	layers := make([]apiv1.DiagnosticLayer, 0, len(manifest.Layers))
	for _, layer := range manifest.Layers {
		legend := make([]apiv1.DiagnosticLegendEntry, 0, len(layer.Legend))
		for _, item := range layer.Legend {
			var numericValue *float32
			if item.Value != nil {
				converted := float32(*item.Value)
				numericValue = &converted
			}
			legend = append(legend, apiv1.DiagnosticLegendEntry{
				Label: item.Label, Color: item.Color, Value: numericValue, Code: item.Code,
			})
		}
		var bounds *[]float32
		if len(layer.Bounds) == 4 {
			converted := make([]float32, len(layer.Bounds))
			for index, value := range layer.Bounds {
				converted[index] = float32(value)
			}
			bounds = &converted
		}
		var elevation *float32
		if layer.ElevationDeg != nil {
			converted := float32(*layer.ElevationDeg)
			elevation = &converted
		}
		var maximumRange *float32
		if layer.MaximumRangeKM != nil {
			converted := float32(*layer.MaximumRangeKM)
			maximumRange = &converted
		}
		layers = append(layers, apiv1.DiagnosticLayer{
			LayerId: layer.LayerID, Title: layer.Title,
			Scope: apiv1.DiagnosticLayerScope(layer.Scope), Field: layer.Field,
			Rendering: apiv1.DiagnosticLayerRendering(layer.Rendering), Unit: layer.Unit,
			ImageUrl: fmt.Sprintf("/api/v1/diagnostics/%s/layers/%s", value.JobID, layer.LayerID),
			Width:    layer.Width, Height: layer.Height,
			PaletteVersion: layer.PaletteVersion, Legend: legend,
			Bounds: bounds, RadarId: layer.RadarID, ScanId: layer.ScanID,
			SweepNumber: layer.SweepNumber, ElevationDeg: elevation,
			MaximumRangeKm: maximumRange,
		})
	}
	return apiv1.DiagnosticBundle{
		ContractVersion: manifest.ContractVersion,
		JobId:           value.JobID, AnalysisId: manifest.AnalysisID,
		AnalysisTime: manifest.AnalysisTime.UTC(), GridId: manifest.GridID,
		DiagnosticConfigVersion: manifest.DiagnosticConfig,
		RendererVersion:         manifest.RendererVersion,
		PaletteVersion:          manifest.PaletteVersion,
		FlagDefinitionVersion:   manifest.FlagDefinitionVersion,
		OperationalEligible:     manifest.OperationalEligible,
		OperationalReasons:      manifest.OperationalReasons,
		Layers:                  layers, CreatedAt: manifest.CreatedAt.UTC(),
	}
}

func toAPIProduct(product workflow.Product) apiv1.Product {
	validTimes := make([]time.Time, len(product.ValidTimes))
	for index, value := range product.ValidTimes {
		validTimes[index] = value.UTC()
	}
	return apiv1.Product{
		ProductId: product.ID, RunId: product.RunID,
		ProductType: apiv1.ProductType(product.ProductType),
		ModelId:     product.ModelID, ModelVersion: product.ModelVersion,
		ConfigVersion: product.ConfigVersion, GridId: product.GridID,
		IssueTime: product.IssueTime.UTC(), ValidTimes: validTimes,
		MemberCount:          product.MemberCount,
		SourceForecastUri:    product.SourceForecastURI,
		SourceForecastSha256: product.SourceForecastSHA256,
		CreatedAt:            product.CreatedAt.UTC(),
	}
}

func toAPIProductAsset(asset workflow.ProductAsset) apiv1.ProductAsset {
	var metadata struct {
		Unit             string   `json:"unit"`
		CoverageRatio    *float64 `json:"coverage_ratio"`
		ValidCellCount   *int64   `json:"valid_cell_count"`
		MissingCellCount *int64   `json:"missing_cell_count"`
		NoRainCellCount  *int64   `json:"no_rain_cell_count"`
	}
	_ = json.Unmarshal(asset.Metadata, &metadata)
	var unit *string
	if metadata.Unit != "" {
		unit = &metadata.Unit
	}
	return apiv1.ProductAsset{
		AssetId: asset.ID, AssetType: asset.AssetType,
		Uri: asset.ObjectURI,
		ContentUrl: fmt.Sprintf(
			"/api/v1/products/%s/assets/%s/content", asset.ProductID, asset.ID,
		),
		MediaType: asset.MediaType, Sha256: asset.SHA256, SizeBytes: asset.SizeBytes,
		LeadTimeMinutes: asset.LeadMinutes, ValidTime: utcPointer(asset.ValidTime),
		Unit: unit, CoverageRatio: float32Pointer(metadata.CoverageRatio),
		ValidCellCount:   metadata.ValidCellCount,
		MissingCellCount: metadata.MissingCellCount,
		NoRainCellCount:  metadata.NoRainCellCount,
		CreatedAt:        asset.CreatedAt.UTC(),
	}
}

func toAPIEnsembleProductBundle(
	bundle ensembleproductstore.Bundle,
) apiv1.EnsembleProductBundle {
	layers := make([]apiv1.EnsembleProductLayer, 0, len(bundle.Layers))
	for _, layer := range bundle.Layers {
		legend := make([]apiv1.EnsembleProductLegendEntry, 0, len(layer.Legend))
		for _, entry := range layer.Legend {
			legend = append(legend, apiv1.EnsembleProductLegendEntry{
				Minimum: float32(entry.Minimum), Color: entry.Color,
			})
		}
		assets := make([]apiv1.EnsembleProductAsset, 0, len(layer.Assets))
		for _, asset := range layer.Assets {
			assets = append(assets, apiv1.EnsembleProductAsset{
				AssetId:   asset.AssetID,
				AssetType: apiv1.EnsembleProductAssetAssetType(asset.AssetType),
				ContentUrl: fmt.Sprintf(
					"/api/v1/ensemble-products/%s/assets/%s", bundle.BundleID, asset.AssetID,
				),
				MediaType: asset.MediaType, Sha256: asset.SHA256, SizeBytes: asset.SizeBytes,
				LeadTimeMinutes: asset.LeadMinutes, ValidTime: asset.ValidTime.UTC(), Unit: asset.Unit,
				CoverageRatio:  float32(asset.CoverageRatio),
				ValidCellCount: asset.ValidCellCount, MissingCellCount: asset.MissingCellCount,
			})
		}
		layers = append(layers, apiv1.EnsembleProductLayer{
			LayerId: layer.LayerID, ProductType: apiv1.ProductType(layer.ProductType),
			VariableName: layer.VariableName, ThresholdMmH: float32Pointer(layer.ThresholdMMH),
			Quantile: float32Pointer(layer.Quantile), Unit: layer.Unit,
			Legend: legend, Assets: assets,
		})
	}
	return apiv1.EnsembleProductBundle{
		BundleId: bundle.BundleID, RunId: bundle.RunID, IssueTime: bundle.IssueTime.UTC(),
		GridId: bundle.GridID, PixelEdgeBounds: bundle.PixelEdgeBounds,
		Width: bundle.Width, Height: bundle.Height,
		ModelId: bundle.ModelID, ModelVersion: bundle.ModelVersion,
		ModelConfigVersion:   bundle.ModelConfigVersion,
		ProductConfigVersion: bundle.ProductConfigVersion,
		MemberCount:          bundle.MemberCount,
		CalibrationStatus:    apiv1.EnsembleProductBundleCalibrationStatus(bundle.CalibrationStatus),
		OperationalEligible:  apiv1.EnsembleProductBundleOperationalEligible(bundle.OperationalEligible),
		OperationalGate:      bundle.OperationalGate,
		SourceForecastUri:    bundle.SourceForecast.URI,
		SourceForecastSha256: bundle.SourceForecast.SHA256,
		Layers:               layers, CreatedAt: bundle.CreatedAt.UTC(),
	}
}

func toAPIAlgorithmVerificationRun(
	run verificationstore.RunSummary,
) apiv1.AlgorithmVerificationRunSummary {
	return apiv1.AlgorithmVerificationRunSummary{
		ProfileVersion:                run.ProfileVersion,
		RunId:                         run.RunID,
		SchemaVersion:                 run.SchemaVersion,
		VerificationKind:              apiv1.AlgorithmVerificationRunSummaryVerificationKind(run.VerificationKind),
		PrimaryTruthKind:              run.PrimaryTruthKind,
		OperationalEligible:           run.OperationalEligible,
		CompletedIssueCount:           run.CompletedIssueCount,
		FailedIssueCount:              run.FailedIssueCount,
		MotionFallbackIssueCount:      run.MotionFallbackIssueCount,
		MetricRowCount:                run.MetricRowCount,
		SkillStatus:                   run.SkillStatus,
		MapsAvailable:                 run.MapsAvailable,
		MapBundleCount:                run.MapBundleCount,
		MapLayerCount:                 run.MapLayerCount,
		MapRendererVersion:            stringPointerOrNil(run.MapRendererVersion),
		ProbabilityMapsAvailable:      run.ProbabilityMapsAvailable,
		ProbabilityMapBundleCount:     run.ProbabilityMapBundleCount,
		ProbabilityMapLayerCount:      run.ProbabilityMapLayerCount,
		ProbabilityMapRendererVersion: stringPointerOrNil(run.ProbabilityMapRendererVersion),
		ModifiedAt:                    run.ModifiedAt.UTC(),
	}
}

func toAPIAlgorithmVerificationProbabilityMapFrame(
	frame verificationstore.ProbabilityMapFrame,
) apiv1.AlgorithmVerificationProbabilityMapFrame {
	layers := make([]apiv1.AlgorithmVerificationProbabilityMapLayer, 0, len(frame.Layers))
	issueKey := frame.IssueTime.UTC().Format("20060102T150405Z")
	for _, layer := range frame.Layers {
		layers = append(layers, apiv1.AlgorithmVerificationProbabilityMapLayer{
			AssetId: layer.AssetID,
			Role:    apiv1.AlgorithmVerificationProbabilityMapLayerRole(layer.Role),
			Model:   layer.Model, LeadMinutes: layer.LeadMinutes,
			ThresholdMmH: layer.ThresholdMMH, ValidTime: layer.ValidTime.UTC(),
			ImageUrl: fmt.Sprintf(
				"/api/v1/algorithm-verification/runs/%s/%s/probability-map-assets/%s/%s/%s",
				frame.ProfileVersion, frame.RunID, frame.CaseID, issueKey, layer.AssetID,
			),
			Width: layer.Width, Height: layer.Height, Sha256: layer.SHA256,
			SizeBytes: layer.SizeBytes, ValidCellCount: layer.ValidCellCount,
			NoEventCellCount: layer.NoEventCellCount, EventCellCount: layer.EventCellCount,
			MissingCellCount: layer.MissingCellCount,
		})
	}
	legend := make([]apiv1.AlgorithmVerificationProbabilityMapLegendEntry, 0, len(frame.Legend))
	for _, item := range frame.Legend {
		legend = append(legend, apiv1.AlgorithmVerificationProbabilityMapLegendEntry{
			MinimumProbabilityPercent: item.MinimumProbabilityPercent, Color: item.Color,
		})
	}
	return apiv1.AlgorithmVerificationProbabilityMapFrame{
		ContractVersion: frame.ContractVersion, RendererVersion: frame.RendererVersion,
		PaletteVersion: frame.PaletteVersion, ProfileVersion: frame.ProfileVersion,
		RunId: frame.RunID, CaseId: frame.CaseID, IssueTime: frame.IssueTime.UTC(),
		ValidTime: frame.ValidTime.UTC(), LeadMinutes: frame.LeadMinutes,
		ThresholdMmH: frame.ThresholdMMH, TruthKind: frame.TruthKind,
		CalibrationStatus:         apiv1.AlgorithmVerificationProbabilityMapFrameCalibrationStatus(frame.CalibrationStatus),
		OperationalEligible:       apiv1.AlgorithmVerificationProbabilityMapFrameOperationalEligible(frame.OperationalEligible),
		ProductPublicationEnabled: apiv1.AlgorithmVerificationProbabilityMapFrameProductPublicationEnabled(frame.ProductPublicationEnabled),
		Projection:                apiv1.AlgorithmVerificationProbabilityMapFrameProjection(frame.Projection),
		PixelEdgeBounds:           frame.PixelEdgeBounds, FitBounds: frame.FitBounds,
		Width: frame.Width, Height: frame.Height,
		ValidNoEventColor: frame.ValidNoEventColor, Legend: legend, Layers: layers,
	}
}

func toAPIAlgorithmVerificationMapFrame(
	frame verificationstore.MapFrame,
) apiv1.AlgorithmVerificationMapFrame {
	layers := make([]apiv1.AlgorithmVerificationMapLayer, 0, len(frame.Layers))
	issueKey := frame.IssueTime.UTC().Format("20060102T150405Z")
	for _, layer := range frame.Layers {
		layers = append(layers, apiv1.AlgorithmVerificationMapLayer{
			AssetId: layer.AssetID, Role: apiv1.AlgorithmVerificationMapLayerRole(layer.Role),
			Model: layer.Model, LeadMinutes: layer.LeadMinutes, ValidTime: layer.ValidTime.UTC(),
			ImageUrl: fmt.Sprintf(
				"/api/v1/algorithm-verification/runs/%s/%s/map-assets/%s/%s/%s",
				frame.ProfileVersion, frame.RunID, frame.CaseID, issueKey, layer.AssetID,
			),
			Width: layer.Width, Height: layer.Height, Sha256: layer.SHA256,
			SizeBytes: layer.SizeBytes, ValidCellCount: layer.ValidCellCount,
			NoRainCellCount: layer.NoRainCellCount, RainCellCount: layer.RainCellCount,
			MissingCellCount: layer.MissingCellCount,
		})
	}
	vectors := make([]apiv1.AlgorithmVerificationMapMotionVector, 0, len(frame.Motion.Vectors))
	for _, vector := range frame.Motion.Vectors {
		vectors = append(vectors, apiv1.AlgorithmVerificationMapMotionVector{
			Longitude: vector.Longitude, Latitude: vector.Latitude,
			EndLongitude: vector.EndLongitude, EndLatitude: vector.EndLatitude,
			UPixelsPerStep: vector.UPixelsPerStep, VPixelsPerStep: vector.VPixelsPerStep,
		})
	}
	legend := make([]apiv1.AlgorithmVerificationMapLegendEntry, 0, len(frame.Legend))
	for _, item := range frame.Legend {
		legend = append(legend, apiv1.AlgorithmVerificationMapLegendEntry{
			MinimumMmH: item.MinimumMMH, Color: item.Color,
		})
	}
	return apiv1.AlgorithmVerificationMapFrame{
		ContractVersion: frame.ContractVersion, RendererVersion: frame.RendererVersion,
		PaletteVersion: frame.PaletteVersion, ProfileVersion: frame.ProfileVersion,
		RunId: frame.RunID, CaseId: frame.CaseID, IssueTime: frame.IssueTime.UTC(),
		ValidTime: frame.ValidTime.UTC(), LeadMinutes: frame.LeadMinutes,
		TruthKind: frame.TruthKind, OperationalEligible: frame.OperationalEligible,
		Projection:      apiv1.AlgorithmVerificationMapFrameProjection(frame.Projection),
		PixelEdgeBounds: frame.PixelEdgeBounds, FitBounds: frame.FitBounds,
		Width: frame.Width, Height: frame.Height,
		RainThresholdMmH: frame.RainThresholdMMH,
		ValidNoRainColor: frame.ValidNoRainColor, Legend: legend, Layers: layers,
		Motion: apiv1.AlgorithmVerificationMapMotion{
			FallbackUsed:            frame.Motion.FallbackUsed,
			FallbackReason:          frame.Motion.FallbackReason,
			FeatureCount:            frame.Motion.FeatureCount,
			TrackableRainPixelCount: frame.Motion.TrackableRainPixelCount,
			Unit:                    frame.Motion.Unit, Vectors: vectors,
		},
	}
}

func stringPointerOrNil(value string) *string {
	if value == "" {
		return nil
	}
	return &value
}

func toAPIAlgorithmVerificationRunDetail(
	detail verificationstore.RunDetail,
) apiv1.AlgorithmVerificationRunDetail {
	cases := make([]apiv1.AlgorithmVerificationCase, 0, len(detail.Cases))
	for _, item := range detail.Cases {
		issueTimes := make([]time.Time, 0, len(item.IssueTimes))
		for _, issueTime := range item.IssueTimes {
			issueTimes = append(issueTimes, issueTime.UTC())
		}
		cases = append(cases, apiv1.AlgorithmVerificationCase{
			CaseId: item.CaseID, Category: item.Category, IssueTimes: issueTimes,
		})
	}
	comparisons := make([]apiv1.AlgorithmVerificationSkillComparison, 0, len(detail.SkillSummary.Comparisons))
	for _, comparison := range detail.SkillSummary.Comparisons {
		comparisons = append(comparisons, apiv1.AlgorithmVerificationSkillComparison{
			Baseline:                    comparison.Baseline,
			BootstrapSampleCount:        comparison.BootstrapSampleCount,
			CaseMeanDifferences:         comparison.CaseMeanDifferences,
			EvaluableCaseCount:          comparison.EvaluableCaseCount,
			MaximumLeadMinutes:          comparison.MaximumLeadMinutes,
			MeanDifference95pctInterval: comparison.MeanDifference95pctInterval,
			MeanFssDifference:           comparison.MeanFSSDifference,
			PassesCaseGate:              comparison.PassesCaseGate,
			PositiveCaseCount:           comparison.PositiveCaseCount,
			ThresholdMmH:                comparison.ThresholdMMH,
			TotalWetCaseCount:           comparison.TotalWetCaseCount,
			WindowPixels:                comparison.WindowPixels,
		})
	}
	fssScales := make([]apiv1.AlgorithmVerificationFSSScale, 0, len(detail.Filters.FSSScales))
	for _, scale := range detail.Filters.FSSScales {
		fssScales = append(fssScales, apiv1.AlgorithmVerificationFSSScale{
			WindowPixels: scale.WindowPixels,
			TargetKm:     scale.TargetKM,
			ActualKmMin:  scale.ActualKMMin,
			ActualKmMax:  scale.ActualKMMax,
		})
	}
	result := apiv1.AlgorithmVerificationRunDetail{
		Run:   toAPIAlgorithmVerificationRun(detail.Run),
		Cases: cases,
		Filters: apiv1.AlgorithmVerificationFilterOptions{
			Models: detail.Filters.Models, LeadMinutes: detail.Filters.LeadMinutes,
			ThresholdsMmH: detail.Filters.ThresholdsMMH,
			WindowsPixels: detail.Filters.WindowsPixels,
			FssScales:     fssScales,
		},
		SkillSummary: apiv1.AlgorithmVerificationSkillSummary{
			Status:           detail.SkillSummary.Status,
			ComparisonMetric: detail.SkillSummary.ComparisonMetric,
			Comparisons:      comparisons,
		},
	}
	if detail.ProbabilisticSummary != nil {
		result.ProbabilisticSummary = toAPIAlgorithmVerificationProbabilisticSummary(
			*detail.ProbabilisticSummary,
		)
	}
	return result
}

func toAPIAlgorithmVerificationProbabilisticSummary(
	summary verificationstore.ProbabilisticSummary,
) *apiv1.AlgorithmVerificationProbabilisticSummary {
	bands := make([]apiv1.AlgorithmVerificationProbabilisticLeadBand, 0, len(summary.LeadBands))
	for _, band := range summary.LeadBands {
		scores := make([]apiv1.AlgorithmVerificationProbabilisticModelScore, 0, len(band.Scores))
		for _, score := range band.Scores {
			scores = append(scores, apiv1.AlgorithmVerificationProbabilisticModelScore{
				Model:                 score.Model,
				BrierScoreByThreshold: score.BrierScoreByThreshold,
				CrpsMmH:               score.CRPSMMH,
				EnsembleMeanRmseMmH:   score.EnsembleMeanRMSEMMH,
				MeanEnsembleSpreadMmH: score.MeanEnsembleSpreadMMH,
			})
		}
		skills := make([]apiv1.AlgorithmVerificationProbabilisticSkill, 0, len(band.CandidateSkills))
		for _, skill := range band.CandidateSkills {
			skills = append(skills, apiv1.AlgorithmVerificationProbabilisticSkill{
				Baseline:              skill.Baseline,
				BrierSkillByThreshold: skill.BrierSkillByThreshold,
				CrpsSkill:             skill.CRPSSkill,
			})
		}
		bands = append(bands, apiv1.AlgorithmVerificationProbabilisticLeadBand{
			Band:                               apiv1.AlgorithmVerificationProbabilisticLeadBandBand(band.Band),
			MinimumLeadMinutes:                 band.MinimumLeadMinutes,
			MaximumLeadMinutes:                 band.MaximumLeadMinutes,
			MinimumCommonVerificationCoverage:  band.MinimumCommonVerificationCoverage,
			MinimumCandidateMemberMeanCoverage: band.MinimumCandidateMemberMeanCoverage,
			MinimumReferenceMemberMeanCoverage: band.MinimumReferenceMemberMeanCoverage,
			Scores:                             scores,
			CandidateSkills:                    skills,
		})
	}
	quantiles := func(value verificationstore.RuntimeQuantiles) apiv1.AlgorithmVerificationRuntimeQuantiles {
		return apiv1.AlgorithmVerificationRuntimeQuantiles{
			Max: value.Maximum,
			P50: value.P50,
			P95: value.P95,
		}
	}
	return &apiv1.AlgorithmVerificationProbabilisticSummary{
		Split:                     apiv1.AlgorithmVerificationProbabilisticSummarySplit(summary.Split),
		CalibrationStatus:         summary.CalibrationStatus,
		ProductPublicationEnabled: summary.ProductPublicationEnabled,
		CandidateModel:            summary.CandidateModel,
		ReferenceModel:            summary.ReferenceModel,
		CandidateMemberCount:      summary.CandidateMemberCount,
		ReferenceMemberCount:      summary.ReferenceMemberCount,
		DeviceName:                summary.DeviceName,
		LeadBands:                 bands,
		Performance: apiv1.AlgorithmVerificationProbabilisticPerformance{
			CandidateRuntimeMs:    quantiles(summary.Performance.CandidateRuntimeMS),
			ReferenceRuntimeMs:    quantiles(summary.Performance.ReferenceRuntimeMS),
			TotalRuntimeMs:        quantiles(summary.Performance.TotalRuntimeMS),
			GpuPeakAllocatedBytes: quantiles(summary.Performance.GPUPeakAllocatedBytes),
			PeakRssBytes:          quantiles(summary.Performance.PeakRSSBytes),
		},
	}
}

func toAPIAlgorithmVerificationMetric(
	metric verificationstore.Metric,
) apiv1.AlgorithmVerificationMetric {
	return apiv1.AlgorithmVerificationMetric{
		CaseId: metric.CaseID, CaseCategory: metric.CaseCategory,
		IssueTime: metric.IssueTime.UTC(), TruthKind: metric.TruthKind,
		Model: metric.Model, LeadMinutes: metric.LeadMinutes,
		ThresholdMmH: metric.ThresholdMMH, WindowPixels: metric.WindowPixels,
		WindowKm: metric.WindowKM, WindowTargetKm: metric.WindowTargetKM,
		Hits: metric.Hits, Misses: metric.Misses,
		FalseAlarms: metric.FalseAlarms, CorrectNegatives: metric.CorrectNegatives,
		Csi: metric.CSI, Pod: metric.POD, Far: metric.FAR, Fss: metric.FSS,
		MaeMmH: metric.MAEMMH, RmseMmH: metric.RMSEMMH,
		MeanErrorMmH: metric.MeanErrorMMH, TruthCoverage: metric.TruthCoverage,
		ForecastCoverage: metric.ForecastCoverage, CommonCoverage: metric.CommonCoverage,
		ForecastToTruthCoverage:                 metric.ForecastToTruthCoverage,
		AdvectionDomainToTruthCoverage:          metric.AdvectionDomainToTruthCoverage,
		AdvectionBoundaryLossRatio:              metric.AdvectionBoundaryLossRatio,
		InteriorMissingLossRatio:                metric.InteriorMissingLossRatio,
		BoundaryAdjustedForecastToTruthCoverage: metric.BoundaryAdjustedCoverage,
		CoverageDecompositionClosureError:       metric.CoverageDecompositionClosureErr,
	}
}

func utcPointer(value *time.Time) *time.Time {
	if value == nil {
		return nil
	}
	utc := value.UTC()
	return &utc
}

func float32Pointer(value *float64) *float32 {
	if value == nil {
		return nil
	}
	converted := float32(*value)
	return &converted
}

func float32ValuePointer(value *float64) *float32 {
	return float32Pointer(value)
}

func writeStoreError(response http.ResponseWriter, err error) {
	if errors.Is(err, workflow.ErrNotFound) {
		writeError(response, http.StatusNotFound, "not_found", "resource was not found")
		return
	}
	if errors.Is(err, orchestration.ErrUnsupportedRerun) {
		writeError(response, http.StatusConflict, "unsupported_rerun", "the selected source run or preset cannot be regenerated from committed lineage")
		return
	}
	if errors.Is(err, orchestration.ErrRegenerationActive) {
		writeError(response, http.StatusConflict, "regeneration_active", "a matching regeneration is already running")
		return
	}
	writeError(response, http.StatusInternalServerError, "internal_error", "control-plane operation failed")
}

func writeProductObjectError(response http.ResponseWriter, err error) {
	if errors.Is(err, objectstore.ErrNotFound) {
		writeError(response, http.StatusNotFound, "not_found", "product object was not found")
		return
	}
	writeError(
		response,
		http.StatusBadGateway,
		"object_store_error",
		"product object could not be read",
	)
}

func writeAlgorithmVerificationError(response http.ResponseWriter, err error) {
	if errors.Is(err, verificationstore.ErrNotFound) {
		writeError(response, http.StatusNotFound, "not_found", "algorithm-verification result was not found")
		return
	}
	if errors.Is(err, verificationstore.ErrInvalidReport) {
		writeError(response, http.StatusBadGateway, "verification_report_invalid", "algorithm-verification report is invalid")
		return
	}
	writeError(response, http.StatusInternalServerError, "internal_error", "algorithm-verification query failed")
}

func writeEnsembleProductError(response http.ResponseWriter, err error) {
	if errors.Is(err, ensembleproductstore.ErrNotFound) {
		writeError(response, http.StatusNotFound, "not_found", "offline ensemble product was not found")
		return
	}
	if errors.Is(err, ensembleproductstore.ErrInvalidBundle) {
		writeError(
			response,
			http.StatusBadGateway,
			"ensemble_product_invalid",
			"offline ensemble product bundle is invalid",
		)
		return
	}
	writeError(response, http.StatusInternalServerError, "internal_error", "offline ensemble product query failed")
}

func validProductSignature(mediaType string, data []byte) bool {
	switch mediaType {
	case "image/png":
		return len(data) >= 8 && string(data[:8]) == "\x89PNG\r\n\x1a\n"
	case "image/tiff; application=geotiff; profile=cloud-optimized":
		return len(data) >= 4 &&
			(string(data[:4]) == "II*\x00" || string(data[:4]) == "MM\x00*")
	case "application/x-netcdf":
		return len(data) >= 4 && (string(data[:4]) == "CDF\x01" || string(data[:4]) == "CDF\x02")
	case "application/vnd.rainpulse.point-index":
		return len(data) >= 8 && string(data[:8]) == "RPPNTV1\x00"
	default:
		return false
	}
}

func writeServiceUnavailable(response http.ResponseWriter) {
	writeError(response, http.StatusServiceUnavailable, "service_unavailable", "control-plane persistence is unavailable")
}

func writeError(response http.ResponseWriter, status int, code, message string) {
	writeJSON(response, status, apiv1.ErrorResponse{
		Code:    code,
		Message: message,
		TraceId: uuid.New(),
	})
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(value)
}

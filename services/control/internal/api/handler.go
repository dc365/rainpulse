package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"time"

	apiv1 "github.com/fonwee/rainpulse-nowcast/services/control/internal/api/generated"
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
	Rerun(context.Context, uuid.UUID) (workflow.Run, error)
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
	ListAnalysisCycles(context.Context, int, *workflow.AnalysisStatus) ([]workflow.AnalysisCycle, error)
	GetAnalysisCycle(context.Context, uuid.UUID) (workflow.AnalysisCycle, error)
}

type Options struct {
	Version         string
	Runs            RunStore
	Observations    ObservationStore
	Commands        RunCommands
	SSEPollInterval time.Duration
}

type server struct {
	apiv1.Unimplemented
	version         string
	runs            RunStore
	observations    ObservationStore
	commands        RunCommands
	ssePollInterval time.Duration
}

func NewHandler(options Options) http.Handler {
	pollInterval := options.SSEPollInterval
	if pollInterval <= 0 {
		pollInterval = time.Second
	}
	return apiv1.HandlerWithOptions(&server{
		version:         options.Version,
		runs:            options.Runs,
		observations:    options.Observations,
		commands:        options.Commands,
		ssePollInterval: pollInterval,
	}, apiv1.ChiServerOptions{BaseURL: "/api/v1"})
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
	limit := 50
	if params.Limit != nil {
		limit = *params.Limit
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
	run, err := service.commands.Rerun(request.Context(), runID)
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
	limit := 50
	if params.Limit != nil {
		limit = *params.Limit
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
	limit := 50
	if params.Limit != nil {
		limit = *params.Limit
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
	writeError(response, http.StatusInternalServerError, "internal_error", "control-plane operation failed")
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

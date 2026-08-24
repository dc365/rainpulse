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

type Options struct {
	Version         string
	Runs            RunStore
	Commands        RunCommands
	SSEPollInterval time.Duration
}

type server struct {
	apiv1.Unimplemented
	version         string
	runs            RunStore
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
		commands:        options.Commands,
		ssePollInterval: pollInterval,
	}, apiv1.ChiServerOptions{BaseURL: "/api/v1"})
}

func (service *server) GetSystemStatus(response http.ResponseWriter, request *http.Request) {
	status := apiv1.Ready
	if service.runs != nil {
		ctx, cancel := context.WithTimeout(request.Context(), 2*time.Second)
		defer cancel()
		if err := service.runs.Ping(ctx); err != nil {
			status = apiv1.Degraded
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

func (service *server) StreamEvents(response http.ResponseWriter, request *http.Request, params apiv1.StreamEventsParams) {
	if service.runs == nil {
		writeServiceUnavailable(response)
		return
	}
	flusher, ok := response.(http.Flusher)
	if !ok {
		writeError(response, http.StatusInternalServerError, "stream_unsupported", "response streaming is unavailable")
		return
	}

	load := service.runs.LatestRun
	if params.RunId != nil {
		runID := *params.RunId
		load = func(ctx context.Context) (workflow.Run, error) {
			return service.runs.GetRun(ctx, runID)
		}
	}
	run, err := load(request.Context())
	if err != nil {
		writeStoreError(response, err)
		return
	}

	response.Header().Set("Content-Type", "text/event-stream")
	response.Header().Set("Cache-Control", "no-cache")
	response.Header().Set("Connection", "keep-alive")
	response.Header().Set("X-Accel-Buffering", "no")
	response.WriteHeader(http.StatusOK)
	if err := writeRunEvent(response, run); err != nil {
		return
	}
	flusher.Flush()

	lastID := run.ID
	lastUpdated := run.UpdatedAt
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
			if err := writeRunEvent(response, current); err != nil {
				return
			}
			flusher.Flush()
			lastID = current.ID
			lastUpdated = current.UpdatedAt
		}
	}
}

func writeRunEvent(response http.ResponseWriter, run workflow.Run) error {
	data, err := json.Marshal(toAPIRun(run))
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(response, "id: %s:%d\nevent: run.updated\ndata: %s\n\n", run.ID, run.UpdatedAt.UnixNano(), data)
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

func utcPointer(value *time.Time) *time.Time {
	if value == nil {
		return nil
	}
	utc := value.UTC()
	return &utc
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

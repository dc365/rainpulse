package workspace

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
)

const (
	workspaceSamplePath   = "/api/v1/workspace/sample"
	workspaceEventsPath   = "/api/v1/workspace/events"
	workspaceStageSuffix  = "/stages"
	workspaceCancelPrefix = "/api/v1/admin/regenerations/"
	workspaceCancelSuffix = "/cancel"
)

type RuntimeOptions struct {
	Store           RuntimeStore
	ProjectionStore ProjectionStore
	Objects         RuntimeObjectReader
	EnsembleRoot    string
	NowcastNetRoot  string
	AdminToken      string
	Now             func() time.Time
}

type runtimeHandler struct {
	next           http.Handler
	workspace      http.Handler
	store          RuntimeStore
	objects        RuntimeObjectReader
	ensembleRoot   string
	nowcastNetRoot string
	adminToken     string
	now            func() time.Time
}

// NewRuntimeHandler composes the browser projection, PostgreSQL materialized
// read model, bounded in-process cache and the exact-sample/pipeline endpoints.
// The existing domain API remains authoritative and is still reachable through
// the wrapped core handler.
func NewRuntimeHandler(core http.Handler, options RuntimeOptions) http.Handler {
	projection := NewHandler(core)
	projected := http.Handler(projection)
	if options.ProjectionStore != nil && environmentBool(
		"RAINPULSE_WORKSPACE_PERSISTENT_PROJECTION_ENABLED",
		true,
	) {
		projected = WithPersistentProjection(projected, options.ProjectionStore, PersistentProjectionOptions{
			CatalogTTL: environmentDuration(
				"RAINPULSE_WORKSPACE_CATALOG_CACHE_TTL",
				defaultWorkspaceCatalogCacheTTL,
			),
			CycleTTL: environmentDuration(
				"RAINPULSE_WORKSPACE_CYCLE_CACHE_TTL",
				defaultWorkspaceCycleCacheTTL,
			),
			StaleIfError: environmentDuration(
				"RAINPULSE_WORKSPACE_CACHE_STALE_IF_ERROR",
				defaultWorkspaceStaleIfError,
			),
			Now: options.Now,
		})
	}
	if cacheOptions, enabled := responseCacheOptionsFromEnvironment(); enabled {
		projected = WithResponseCache(projected, cacheOptions)
	}
	now := options.Now
	if now == nil {
		now = time.Now
	}
	return &runtimeHandler{
		next: projected, workspace: projected,
		store: options.Store, objects: options.Objects,
		ensembleRoot:   strings.TrimSpace(options.EnsembleRoot),
		nowcastNetRoot: strings.TrimSpace(options.NowcastNetRoot),
		adminToken:     options.AdminToken, now: now,
	}
}

func (handler *runtimeHandler) ServeHTTP(response http.ResponseWriter, request *http.Request) {
	switch {
	case request.Method == http.MethodGet && request.URL.Path == workspaceSamplePath:
		handler.getExactSample(response, request)
		return
	case request.Method == http.MethodGet && request.URL.Path == workspaceEventsPath:
		handler.streamWorkspaceEvents(response, request)
		return
	case request.Method == http.MethodGet && strings.HasPrefix(request.URL.Path, workspacePrefix+"/") &&
		strings.HasSuffix(request.URL.Path, workspaceStageSuffix):
		handler.getPipelineStages(response, request)
		return
	case request.Method == http.MethodPost && strings.HasPrefix(request.URL.Path, workspaceCancelPrefix) &&
		strings.HasSuffix(request.URL.Path, workspaceCancelSuffix):
		handler.cancelRegeneration(response, request)
		return
	default:
		handler.next.ServeHTTP(response, request)
	}
}

func (handler *runtimeHandler) getExactSample(response http.ResponseWriter, request *http.Request) {
	assetURL := strings.TrimSpace(request.URL.Query().Get("asset_url"))
	longitude, longitudeErr := strconv.ParseFloat(request.URL.Query().Get("longitude"), 64)
	latitude, latitudeErr := strconv.ParseFloat(request.URL.Query().Get("latitude"), 64)
	if assetURL == "" || longitudeErr != nil || latitudeErr != nil ||
		!validCoordinate(longitude) || !validCoordinate(latitude) ||
		longitude < -180 || longitude > 180 || latitude < -90 || latitude > 90 {
		runtimeWriteError(response, http.StatusBadRequest, "invalid_sample_request", "asset_url and finite longitude/latitude are required")
		return
	}
	sample, err := handler.sampleAsset(request.Context(), assetURL, longitude, latitude)
	if err != nil {
		switch {
		case errors.Is(err, errUnsupportedSample):
			runtimeWriteError(response, http.StatusNotFound, "exact_sample_unavailable", err.Error())
		case errors.Is(err, errSampleNotFound):
			runtimeWriteError(response, http.StatusNotFound, "sample_source_not_found", err.Error())
		default:
			runtimeWriteError(response, http.StatusBadGateway, "invalid_sample_source", err.Error())
		}
		return
	}
	response.Header().Set("Cache-Control", "no-store")
	writeJSON(response, http.StatusOK, sample)
}

func (handler *runtimeHandler) getPipelineStages(response http.ResponseWriter, request *http.Request) {
	if handler.store == nil {
		runtimeWriteError(response, http.StatusServiceUnavailable, "pipeline_snapshot_unavailable", "pipeline snapshot store is unavailable")
		return
	}
	rawID := strings.TrimSuffix(
		strings.TrimPrefix(request.URL.Path, workspacePrefix+"/"),
		workspaceStageSuffix,
	)
	gridID, issueTime, err := decodeCycleID(rawID)
	if err != nil {
		runtimeWriteError(response, http.StatusBadRequest, "invalid_cycle_id", err.Error())
		return
	}
	snapshot, err := handler.store.WorkspacePipelineSnapshot(request.Context(), gridID, issueTime)
	if err != nil {
		runtimeWriteError(response, http.StatusServiceUnavailable, "pipeline_snapshot_unavailable", err.Error())
		return
	}
	snapshot.SchemaVersion = "1.0"
	snapshot.CycleID = rawID
	snapshot.GridID = gridID
	snapshot.IssueTime = issueTime
	snapshot.GeneratedAt = handler.now().UTC()
	response.Header().Set("Cache-Control", "no-store")
	writeJSON(response, http.StatusOK, snapshot)
}

func (handler *runtimeHandler) cancelRegeneration(response http.ResponseWriter, request *http.Request) {
	if !handler.authorized(request) {
		response.Header().Set("WWW-Authenticate", "Bearer")
		runtimeWriteError(response, http.StatusUnauthorized, "unauthorized", "administrator credentials are required")
		return
	}
	if handler.store == nil {
		runtimeWriteError(response, http.StatusServiceUnavailable, "regeneration_store_unavailable", "regeneration store is unavailable")
		return
	}
	remainder := strings.TrimSuffix(
		strings.TrimPrefix(request.URL.Path, workspaceCancelPrefix),
		workspaceCancelSuffix,
	)
	if strings.Contains(remainder, "/") {
		runtimeWriteError(response, http.StatusNotFound, "not_found", "regeneration was not found")
		return
	}
	requestID, err := uuid.Parse(remainder)
	if err != nil || requestID == uuid.Nil {
		runtimeWriteError(response, http.StatusBadRequest, "invalid_regeneration_id", "regeneration ID must be a UUID")
		return
	}
	var payload struct {
		Reason string `json:"reason"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(response, request.Body, 4096))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&payload); err != nil {
		runtimeWriteError(response, http.StatusBadRequest, "invalid_request", "a JSON cancellation reason is required")
		return
	}
	payload.Reason = strings.TrimSpace(payload.Reason)
	if len([]rune(payload.Reason)) < 3 || len([]rune(payload.Reason)) > 240 {
		runtimeWriteError(response, http.StatusBadRequest, "invalid_reason", "cancellation reason must contain 3 to 240 characters")
		return
	}
	result, err := handler.store.CancelWorkspaceRegeneration(request.Context(), requestID, payload.Reason)
	if err != nil {
		runtimeWriteError(response, http.StatusConflict, "regeneration_cancel_failed", err.Error())
		return
	}
	response.Header().Set("Cache-Control", "no-store")
	writeJSON(response, http.StatusOK, result)
}

func (handler *runtimeHandler) authorized(request *http.Request) bool {
	if handler.adminToken == "" {
		return false
	}
	const prefix = "Bearer "
	authorization := request.Header.Get("Authorization")
	if !strings.HasPrefix(authorization, prefix) {
		return false
	}
	return subtle.ConstantTimeCompare(
		[]byte(strings.TrimPrefix(authorization, prefix)),
		[]byte(handler.adminToken),
	) == 1
}

func (handler *runtimeHandler) streamWorkspaceEvents(response http.ResponseWriter, request *http.Request) {
	flusher, ok := response.(http.Flusher)
	if !ok {
		runtimeWriteError(response, http.StatusInternalServerError, "streaming_unavailable", "HTTP streaming is unavailable")
		return
	}
	response.Header().Set("Content-Type", "text/event-stream")
	response.Header().Set("Cache-Control", "no-cache, no-transform")
	response.Header().Set("Connection", "keep-alive")
	response.Header().Set("X-Accel-Buffering", "no")
	interval := environmentDuration("RAINPULSE_WORKSPACE_EVENT_INTERVAL", 2*time.Second)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	lastETag := ""
	emit := func() bool {
		recorder := httptest.NewRecorder()
		upstream := httptest.NewRequestWithContext(request.Context(), http.MethodGet, workspacePrefix+"?limit=200", nil)
		upstream.Header.Set("Cache-Control", "no-cache")
		handler.workspace.ServeHTTP(recorder, upstream)
		if recorder.Code != http.StatusOK {
			return true
		}
		etag := recorder.Header().Get("ETag")
		if etag == "" {
			etag = responseETag(recorder.Body.Bytes())
		}
		if etag == lastETag {
			return true
		}
		lastETag = etag
		payload, _ := json.Marshal(map[string]any{
			"event":       "workspace.changed",
			"etag":        etag,
			"occurred_at": handler.now().UTC().Format(time.RFC3339),
		})
		if _, err := fmt.Fprintf(response, "event: workspace.changed\ndata: %s\n\n", payload); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}
	if !emit() {
		return
	}
	for {
		select {
		case <-request.Context().Done():
			return
		case <-ticker.C:
			if !emit() {
				return
			}
		}
	}
}

func runtimeWriteError(response http.ResponseWriter, status int, code string, message string) {
	writeJSON(response, status, map[string]string{"code": code, "message": message})
}

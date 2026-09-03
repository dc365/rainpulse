package workspace

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"time"
)

type PersistentProjectionOptions struct {
	CatalogTTL   time.Duration
	CycleTTL     time.Duration
	StaleIfError time.Duration
	Now          func() time.Time
}

type persistentProjectionHandler struct {
	next    http.Handler
	store   ProjectionStore
	options PersistentProjectionOptions
}

// WithPersistentProjection materializes the expensive Workspace JSON responses
// in PostgreSQL. It is intentionally a read-model cache rather than a second
// source of truth: every record has a short expiry and can always be rebuilt
// from the authoritative domain API.
func WithPersistentProjection(
	next http.Handler,
	store ProjectionStore,
	options PersistentProjectionOptions,
) http.Handler {
	if store == nil {
		return next
	}
	if options.CatalogTTL <= 0 {
		options.CatalogTTL = defaultWorkspaceCatalogCacheTTL
	}
	if options.CycleTTL <= 0 {
		options.CycleTTL = defaultWorkspaceCycleCacheTTL
	}
	if options.StaleIfError < 0 {
		options.StaleIfError = defaultWorkspaceStaleIfError
	}
	if options.Now == nil {
		options.Now = time.Now
	}
	return &persistentProjectionHandler{next: next, store: store, options: options}
}

func (handler *persistentProjectionHandler) ServeHTTP(
	response http.ResponseWriter,
	request *http.Request,
) {
	ttl, cacheable := persistentProjectionTTL(request, handler.options)
	if !cacheable {
		handler.next.ServeHTTP(response, request)
		return
	}
	key := request.URL.RequestURI()
	now := handler.options.Now().UTC()
	record, found := handler.load(request.Context(), key)
	forceRefresh := strings.Contains(request.Header.Get("Cache-Control"), "no-cache")
	if found && now.Before(record.ExpiresAt) && !forceRefresh {
		serveProjectionRecord(response, request, record, "PERSISTENT_HIT", 0)
		return
	}

	recorder := httptest.NewRecorder()
	handler.next.ServeHTTP(recorder, request)
	body := append([]byte(nil), recorder.Body.Bytes()...)
	if recorder.Code >= http.StatusOK && recorder.Code < http.StatusMultipleChoices {
		stored := ProjectionRecord{
			Key: key, StatusCode: recorder.Code,
			Header: cloneProjectionHeaders(recorder.Header()), Body: body,
			ETag:      fmt.Sprintf(`"%x"`, sha256.Sum256(body)),
			ExpiresAt: now.Add(ttl), StaleUntil: now.Add(ttl + handler.options.StaleIfError),
			GeneratedAt: now,
		}
		if err := handler.store.SaveWorkspaceProjection(request.Context(), stored); err == nil {
			_ = handler.store.DeleteExpiredWorkspaceProjections(request.Context(), now, 32)
		}
		serveProjectionRecord(response, request, stored, "PERSISTENT_MISS", 0)
		return
	}

	if found && recorder.Code >= http.StatusInternalServerError && now.Before(record.StaleUntil) {
		serveProjectionRecord(response, request, record, "PERSISTENT_STALE", now.Sub(record.ExpiresAt))
		return
	}
	copyHeaders(response.Header(), recorder.Header())
	response.Header().Set("X-RainPulse-Projection", "BYPASS")
	response.WriteHeader(recorder.Code)
	_, _ = response.Write(body)
}

func (handler *persistentProjectionHandler) load(
	ctx context.Context,
	key string,
) (ProjectionRecord, bool) {
	record, err := handler.store.LoadWorkspaceProjection(ctx, key)
	if err != nil {
		return ProjectionRecord{}, false
	}
	return record, true
}

func persistentProjectionTTL(
	request *http.Request,
	options PersistentProjectionOptions,
) (time.Duration, bool) {
	if request.Method != http.MethodGet || request.Header.Get("Authorization") != "" {
		return 0, false
	}
	if request.URL.Path == workspacePrefix {
		return options.CatalogTTL, true
	}
	if !strings.HasPrefix(request.URL.Path, workspacePrefix+"/") {
		return 0, false
	}
	remainder := strings.TrimPrefix(request.URL.Path, workspacePrefix+"/")
	if remainder == "" || strings.Contains(remainder, "/") {
		return 0, false
	}
	return options.CycleTTL, true
}

func serveProjectionRecord(
	response http.ResponseWriter,
	request *http.Request,
	record ProjectionRecord,
	state string,
	staleFor time.Duration,
) {
	for key, values := range record.Header {
		for _, value := range values {
			response.Header().Add(key, value)
		}
	}
	response.Header().Set("ETag", record.ETag)
	response.Header().Set("Cache-Control", "private, max-age=0, must-revalidate")
	response.Header().Set("X-RainPulse-Projection", state)
	if staleFor > 0 {
		response.Header().Set("Warning", `110 RainPulse "workspace projection is stale"`)
		response.Header().Set(
			"X-RainPulse-Stale-Seconds",
			strconv.FormatInt(int64(staleFor/time.Second), 10),
		)
	}
	if etagMatches(request.Header.Get("If-None-Match"), record.ETag) {
		response.WriteHeader(http.StatusNotModified)
		return
	}
	response.Header().Set("Content-Length", strconv.Itoa(len(record.Body)))
	response.WriteHeader(record.StatusCode)
	_, _ = response.Write(record.Body)
}

func cloneProjectionHeaders(source http.Header) HeaderValues {
	result := make(HeaderValues)
	for key, values := range source {
		switch http.CanonicalHeaderKey(key) {
		case "Content-Type", "Vary":
			result[key] = append([]string(nil), values...)
		}
	}
	return result
}

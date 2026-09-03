package workspace

import (
	"crypto/sha256"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	defaultWorkspaceCatalogCacheTTL = 5 * time.Second
	defaultWorkspaceCycleCacheTTL   = 15 * time.Second
	defaultWorkspaceStaleIfError    = 2 * time.Minute
	defaultWorkspaceCacheEntries    = 256
)

// ResponseCacheOptions controls the bounded in-process cache in front of the
// browser-oriented workspace projection. The authoritative domain API and
// immutable product assets remain uncached by this layer.
type ResponseCacheOptions struct {
	CatalogTTL   time.Duration
	CycleTTL     time.Duration
	StaleIfError time.Duration
	MaxEntries   int
	Now          func() time.Time
}

type cachedResponse struct {
	status     int
	header     http.Header
	body       []byte
	etag       string
	expiresAt  time.Time
	staleUntil time.Time
	lastAccess time.Time
}

type responseCacheHandler struct {
	next    http.Handler
	options ResponseCacheOptions

	mu      sync.Mutex
	entries map[string]cachedResponse
}

// NewCachedHandler builds the workspace projection and adds a bounded response
// cache. It prevents the current projection's fan-out reads from repeating on
// every browser poll while retaining short freshness windows and stale-on-error
// continuity for a previously successful cycle response.
func NewCachedHandler(core http.Handler) http.Handler {
	projection := NewHandler(core)
	options, enabled := responseCacheOptionsFromEnvironment()
	if !enabled {
		return projection
	}
	return WithResponseCache(projection, options)
}

// WithResponseCache wraps an existing workspace handler. It is exported so the
// cache semantics can be tested independently from PostgreSQL and object-store
// fixtures.
func WithResponseCache(next http.Handler, options ResponseCacheOptions) http.Handler {
	if options.CatalogTTL <= 0 {
		options.CatalogTTL = defaultWorkspaceCatalogCacheTTL
	}
	if options.CycleTTL <= 0 {
		options.CycleTTL = defaultWorkspaceCycleCacheTTL
	}
	if options.StaleIfError < 0 {
		options.StaleIfError = 0
	}
	if options.MaxEntries <= 0 {
		options.MaxEntries = defaultWorkspaceCacheEntries
	}
	if options.Now == nil {
		options.Now = time.Now
	}
	return &responseCacheHandler{
		next:    next,
		options: options,
		entries: make(map[string]cachedResponse),
	}
}

func (handler *responseCacheHandler) ServeHTTP(
	response http.ResponseWriter,
	request *http.Request,
) {
	ttl, cacheable := handler.ttlFor(request)
	if !cacheable {
		handler.next.ServeHTTP(response, request)
		return
	}

	key := request.URL.RequestURI()
	now := handler.options.Now().UTC()
	entry, found := handler.lookup(key, now)
	forceRefresh := request.Header.Get("Cache-Control") == "no-cache"
	if found && now.Before(entry.expiresAt) && !forceRefresh {
		handler.serve(response, request, entry, "HIT", 0)
		return
	}

	recorder := httptest.NewRecorder()
	handler.next.ServeHTTP(recorder, request)
	result := recorder.Result()
	defer result.Body.Close()

	body := append([]byte(nil), recorder.Body.Bytes()...)
	if result.StatusCode >= http.StatusOK && result.StatusCode < http.StatusMultipleChoices {
		stored := cachedResponse{
			status:     result.StatusCode,
			header:     cloneCacheHeaders(result.Header),
			body:       body,
			etag:       responseETag(body),
			expiresAt:  now.Add(ttl),
			staleUntil: now.Add(ttl + handler.options.StaleIfError),
			lastAccess: now,
		}
		handler.store(key, stored, now)
		handler.serve(response, request, stored, "MISS", 0)
		return
	}

	if found && result.StatusCode >= http.StatusInternalServerError && now.Before(entry.staleUntil) {
		handler.serve(response, request, entry, "STALE", now.Sub(entry.expiresAt))
		return
	}
	copyHeaders(response.Header(), result.Header)
	response.Header().Set("X-RainPulse-Cache", "BYPASS")
	response.WriteHeader(result.StatusCode)
	_, _ = response.Write(body)
}

func (handler *responseCacheHandler) ttlFor(request *http.Request) (time.Duration, bool) {
	if request.Method != http.MethodGet || request.Header.Get("Authorization") != "" {
		return 0, false
	}
	if request.URL.Path == workspacePrefix {
		return handler.options.CatalogTTL, true
	}
	if !strings.HasPrefix(request.URL.Path, workspacePrefix+"/") {
		return 0, false
	}
	remainder := strings.TrimPrefix(request.URL.Path, workspacePrefix+"/")
	if remainder == "" || strings.Contains(remainder, "/") {
		return 0, false
	}
	return handler.options.CycleTTL, true
}

func (handler *responseCacheHandler) lookup(key string, now time.Time) (cachedResponse, bool) {
	handler.mu.Lock()
	defer handler.mu.Unlock()
	entry, found := handler.entries[key]
	if !found {
		return cachedResponse{}, false
	}
	if !now.Before(entry.staleUntil) {
		delete(handler.entries, key)
		return cachedResponse{}, false
	}
	entry.lastAccess = now
	handler.entries[key] = entry
	return entry, true
}

func (handler *responseCacheHandler) store(key string, entry cachedResponse, now time.Time) {
	handler.mu.Lock()
	defer handler.mu.Unlock()
	for candidate, value := range handler.entries {
		if !now.Before(value.staleUntil) {
			delete(handler.entries, candidate)
		}
	}
	if _, exists := handler.entries[key]; !exists && len(handler.entries) >= handler.options.MaxEntries {
		oldestKey := ""
		var oldest time.Time
		for candidate, value := range handler.entries {
			if oldestKey == "" || value.lastAccess.Before(oldest) {
				oldestKey = candidate
				oldest = value.lastAccess
			}
		}
		delete(handler.entries, oldestKey)
	}
	handler.entries[key] = entry
}

func (handler *responseCacheHandler) serve(
	response http.ResponseWriter,
	request *http.Request,
	entry cachedResponse,
	state string,
	staleFor time.Duration,
) {
	copyHeaders(response.Header(), entry.header)
	response.Header().Set("ETag", entry.etag)
	response.Header().Set("Cache-Control", "private, max-age=0, must-revalidate")
	response.Header().Set("X-RainPulse-Cache", state)
	if staleFor > 0 {
		response.Header().Set("Warning", `110 RainPulse "workspace response is stale"`)
		response.Header().Set("X-RainPulse-Stale-Seconds", strconv.FormatInt(int64(staleFor/time.Second), 10))
	}
	if etagMatches(request.Header.Get("If-None-Match"), entry.etag) {
		response.WriteHeader(http.StatusNotModified)
		return
	}
	response.Header().Set("Content-Length", strconv.Itoa(len(entry.body)))
	response.WriteHeader(entry.status)
	_, _ = response.Write(entry.body)
}

func responseETag(body []byte) string {
	return fmt.Sprintf(`"%x"`, sha256.Sum256(body))
}

func etagMatches(header string, expected string) bool {
	for _, candidate := range strings.Split(header, ",") {
		candidate = strings.TrimSpace(candidate)
		if candidate == "*" || candidate == expected || strings.TrimPrefix(candidate, "W/") == expected {
			return true
		}
	}
	return false
}

func cloneCacheHeaders(source http.Header) http.Header {
	result := make(http.Header, len(source))
	copyHeaders(result, source)
	for _, key := range []string{"Age", "Content-Length", "ETag", "Warning", "X-RainPulse-Cache", "X-RainPulse-Stale-Seconds"} {
		result.Del(key)
	}
	return result
}

func copyHeaders(destination http.Header, source http.Header) {
	for key, values := range source {
		destination.Del(key)
		for _, value := range values {
			destination.Add(key, value)
		}
	}
}

func responseCacheOptionsFromEnvironment() (ResponseCacheOptions, bool) {
	enabled := environmentBool("RAINPULSE_WORKSPACE_CACHE_ENABLED", true)
	return ResponseCacheOptions{
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
		MaxEntries: environmentInteger(
			"RAINPULSE_WORKSPACE_CACHE_MAX_ENTRIES",
			defaultWorkspaceCacheEntries,
		),
	}, enabled
}

func environmentBool(name string, fallback bool) bool {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback
	}
	value, err := strconv.ParseBool(raw)
	if err != nil {
		slog.Warn("invalid boolean environment setting", "name", name, "value", raw)
		return fallback
	}
	return value
}

func environmentDuration(name string, fallback time.Duration) time.Duration {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback
	}
	value, err := time.ParseDuration(raw)
	if err != nil || value <= 0 {
		slog.Warn("invalid duration environment setting", "name", name, "value", raw)
		return fallback
	}
	return value
}

func environmentInteger(name string, fallback int) int {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value <= 0 {
		slog.Warn("invalid integer environment setting", "name", name, "value", raw)
		return fallback
	}
	return value
}

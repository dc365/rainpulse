package workspace

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestResponseCacheCachesWorkspaceCycleAndHonorsETag(t *testing.T) {
	now := time.Date(2026, 9, 3, 3, 0, 0, 0, time.UTC)
	calls := 0
	next := http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		calls++
		response.Header().Set("Content-Type", "application/json")
		response.Header().Set("X-Source", "projection")
		_, _ = fmt.Fprintf(response, `{"call":%d}`, calls)
	})
	handler := WithResponseCache(next, ResponseCacheOptions{
		CatalogTTL:   5 * time.Second,
		CycleTTL:     15 * time.Second,
		StaleIfError: time.Minute,
		MaxEntries:   4,
		Now:          func() time.Time { return now },
	})

	first := executeCacheRequest(handler, http.MethodGet, workspacePrefix+"/cycle-a", "")
	if first.Code != http.StatusOK || first.Header().Get("X-RainPulse-Cache") != "MISS" {
		t.Fatalf("first response = %d %q", first.Code, first.Header().Get("X-RainPulse-Cache"))
	}
	etag := first.Header().Get("ETag")
	if etag == "" || first.Body.String() != `{"call":1}` {
		t.Fatalf("unexpected first response: etag=%q body=%q", etag, first.Body.String())
	}

	second := executeCacheRequest(handler, http.MethodGet, workspacePrefix+"/cycle-a", "")
	if second.Code != http.StatusOK || second.Header().Get("X-RainPulse-Cache") != "HIT" {
		t.Fatalf("second response = %d %q", second.Code, second.Header().Get("X-RainPulse-Cache"))
	}
	if calls != 1 || second.Body.String() != first.Body.String() {
		t.Fatalf("cache did not reuse the response: calls=%d body=%q", calls, second.Body.String())
	}

	conditional := executeCacheRequest(handler, http.MethodGet, workspacePrefix+"/cycle-a", etag)
	if conditional.Code != http.StatusNotModified || conditional.Body.Len() != 0 {
		t.Fatalf("conditional response = %d body=%q", conditional.Code, conditional.Body.String())
	}
	if calls != 1 {
		t.Fatalf("conditional request reached projection: calls=%d", calls)
	}
}

func TestResponseCacheServesStaleCycleWhenProjectionFails(t *testing.T) {
	now := time.Date(2026, 9, 3, 3, 0, 0, 0, time.UTC)
	fail := false
	calls := 0
	next := http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		calls++
		if fail {
			http.Error(response, "projection unavailable", http.StatusServiceUnavailable)
			return
		}
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"stable":true}`))
	})
	handler := WithResponseCache(next, ResponseCacheOptions{
		CatalogTTL:   time.Second,
		CycleTTL:     time.Second,
		StaleIfError: time.Minute,
		MaxEntries:   4,
		Now:          func() time.Time { return now },
	})

	first := executeCacheRequest(handler, http.MethodGet, workspacePrefix+"/cycle-b", "")
	if first.Code != http.StatusOK {
		t.Fatalf("prime response = %d", first.Code)
	}
	now = now.Add(2 * time.Second)
	fail = true
	stale := executeCacheRequest(handler, http.MethodGet, workspacePrefix+"/cycle-b", "")
	if stale.Code != http.StatusOK || stale.Header().Get("X-RainPulse-Cache") != "STALE" {
		t.Fatalf("stale response = %d %q", stale.Code, stale.Header().Get("X-RainPulse-Cache"))
	}
	if stale.Body.String() != `{"stable":true}` || stale.Header().Get("Warning") == "" {
		t.Fatalf("unexpected stale response: body=%q warning=%q", stale.Body.String(), stale.Header().Get("Warning"))
	}
	if calls != 2 {
		t.Fatalf("expected one refresh attempt, calls=%d", calls)
	}
}

func TestResponseCacheBypassesNonWorkspaceAndAuthorizedRequests(t *testing.T) {
	calls := 0
	next := http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		calls++
		response.WriteHeader(http.StatusNoContent)
	})
	handler := WithResponseCache(next, ResponseCacheOptions{})

	for _, test := range []struct {
		method        string
		path          string
		authorization string
	}{
		{http.MethodGet, "/api/v1/system/status", ""},
		{http.MethodPost, workspacePrefix, ""},
		{http.MethodGet, workspacePrefix, "Bearer internal"},
		{http.MethodGet, workspacePrefix + "/cycle/assets/x", ""},
	} {
		request := httptest.NewRequest(test.method, test.path, nil)
		request.Header.Set("Authorization", test.authorization)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusNoContent || response.Header().Get("X-RainPulse-Cache") != "" {
			t.Fatalf("bypass %s %s = %d cache=%q", test.method, test.path, response.Code, response.Header().Get("X-RainPulse-Cache"))
		}
	}
	if calls != 4 {
		t.Fatalf("bypass calls=%d", calls)
	}
}

func executeCacheRequest(handler http.Handler, method, path, etag string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, nil)
	if etag != "" {
		request.Header.Set("If-None-Match", etag)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}

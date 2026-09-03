package webgateway_test

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/webgateway"
)

func TestHandlerServesSPAAndProxiesAPI(t *testing.T) {
	webRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(webRoot, "index.html"), []byte("<h1>RainPulse</h1>"), 0o600); err != nil {
		t.Fatalf("write test index: %v", err)
	}

	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/v1/system/status" {
			t.Fatalf("unexpected upstream path %q", request.URL.Path)
		}
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"status":"ready"}`))
	}))
	t.Cleanup(upstream.Close)

	handler, err := webgateway.NewHandler(webgateway.Options{
		WebRoot:    webRoot,
		APIBaseURL: upstream.URL,
	})
	if err != nil {
		t.Fatalf("create web gateway: %v", err)
	}

	for _, testCase := range []struct {
		path        string
		contentType string
		body        string
	}{
		{path: "/", contentType: "text/html", body: "RainPulse"},
		{path: "/radar/nowcast", contentType: "text/html", body: "RainPulse"},
		{path: "/api/v1/system/status", contentType: "application/json", body: `"ready"`},
		{path: "/healthz", contentType: "text/plain", body: "ok"},
	} {
		t.Run(testCase.path, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, testCase.path, nil)
			response := httptest.NewRecorder()

			handler.ServeHTTP(response, request)

			result := response.Result()
			defer result.Body.Close()
			if result.StatusCode != http.StatusOK {
				t.Fatalf("expected 200, got %d", result.StatusCode)
			}
			if got := result.Header.Get("Content-Type"); !strings.HasPrefix(got, testCase.contentType) {
				t.Fatalf("expected content type %q, got %q", testCase.contentType, got)
			}
			if result.Header.Get("X-Content-Type-Options") != "nosniff" ||
				result.Header.Get("Content-Security-Policy") == "" {
				t.Fatal("security headers are missing")
			}
			body, err := io.ReadAll(result.Body)
			if err != nil {
				t.Fatalf("read response: %v", err)
			}
			if !strings.Contains(string(body), testCase.body) {
				t.Fatalf("expected body to contain %q, got %q", testCase.body, body)
			}
		})
	}
}

func TestPublicGatewayOnlyProxiesBoundedForecastRegeneration(t *testing.T) {
	webRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(webRoot, "index.html"), []byte("ok"), 0o600); err != nil {
		t.Fatal(err)
	}
	upstreamCalls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		upstreamCalls++
		if request.URL.Path != "/api/v1/admin/runs/0b390d5f-33e7-4ed8-aab9-8568063dc18c/rerun" ||
			request.Header.Get("Authorization") != "Bearer server-operator-secret" {
			t.Fatalf("unexpected proxied request: path=%q authorization=%q", request.URL.Path, request.Header.Get("Authorization"))
		}
		response.WriteHeader(http.StatusAccepted)
	}))
	t.Cleanup(upstream.Close)
	handler, err := webgateway.NewHandler(webgateway.Options{
		WebRoot: webRoot, APIBaseURL: upstream.URL, AdminToken: "server-operator-secret",
	})
	if err != nil {
		t.Fatal(err)
	}
	allowed := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/admin/runs/0b390d5f-33e7-4ed8-aab9-8568063dc18c/rerun",
		strings.NewReader(`{"preset":"pysteps_lk","reason":"operator validation"}`),
	)
	allowed.Header.Set("Authorization", "Bearer browser-supplied-token")
	allowedResponse := httptest.NewRecorder()
	handler.ServeHTTP(allowedResponse, allowed)
	if allowedResponse.Code != http.StatusAccepted || upstreamCalls != 1 {
		t.Fatalf("allowed response=%d, upstream_calls=%d", allowedResponse.Code, upstreamCalls)
	}

	for _, target := range []string{
		"/api/v1/admin/runs/not-a-uuid/rerun",
		"/api/v1/admin/runs/0b390d5f-33e7-4ed8-aab9-8568063dc18c/delete",
		"/api/v1/admin/system/reload",
	} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodPost, target, nil))
		if response.Code != http.StatusNotFound || upstreamCalls != 1 {
			t.Fatalf("target=%q response=%d upstream_calls=%d", target, response.Code, upstreamCalls)
		}
	}
}

func TestPublicGatewayRejectsRegenerationWhenServerTokenIsMissing(t *testing.T) {
	webRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(webRoot, "index.html"), []byte("ok"), 0o600); err != nil {
		t.Fatal(err)
	}
	upstreamCalled := false
	upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		upstreamCalled = true
	}))
	t.Cleanup(upstream.Close)
	handler, err := webgateway.NewHandler(webgateway.Options{WebRoot: webRoot, APIBaseURL: upstream.URL})
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(
		http.MethodPost,
		"/api/v1/admin/runs/0b390d5f-33e7-4ed8-aab9-8568063dc18c/rerun",
		nil,
	))
	if response.Code != http.StatusServiceUnavailable || upstreamCalled {
		t.Fatalf("response=%d upstream_called=%t", response.Code, upstreamCalled)
	}
}

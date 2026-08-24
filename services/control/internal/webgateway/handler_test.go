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

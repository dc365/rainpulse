package healthcheck

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRunJSONStatusRequiresReadyState(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"status":"degraded"}`))
	}))
	defer server.Close()

	if err := RunJSONStatus(server.URL, "ready"); err == nil {
		t.Fatal("expected degraded dependency status to fail readiness")
	}
}

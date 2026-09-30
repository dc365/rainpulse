package operations

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func basemapConfigHandler(t *testing.T, path string) http.Handler {
	t.Helper()
	return NewHandler(&Service{}, http.NotFoundHandler(), HTTPOptions{AdminToken: "admin", BasemapConfigPath: path})
}

func TestBasemapConfigSeedsIntranetDefaultWhenFileMissing(t *testing.T) {
	handler := basemapConfigHandler(t, t.TempDir()+"/missing.json")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, radarWorkspacePrefix+"basemap-config", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d", recorder.Code)
	}
	var payload struct {
		Default string `json:"default"`
		Sources []struct {
			Key        string `json:"key"`
			Label      string `json:"label"`
			HasOverlay bool   `json:"hasOverlay"`
		} `json:"sources"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.Default != "XZ" {
		t.Fatalf("default = %q, want intranet XZ", payload.Default)
	}
	if len(payload.Sources) != 4 {
		t.Fatalf("sources = %d, want 4", len(payload.Sources))
	}
	if strings.Contains(recorder.Body.String(), "192.168.18.101") {
		t.Fatalf("public payload must not leak upstream URLs: %s", recorder.Body.String())
	}
	for _, source := range payload.Sources {
		if source.Key == "TDT" && !source.HasOverlay {
			t.Fatal("TDT should advertise its annotation overlay")
		}
	}
}

func TestBasemapConfigAdminRoundTripAndValidation(t *testing.T) {
	path := t.TempDir() + "/basemap-config.json"
	handler := basemapConfigHandler(t, path)

	custom := BasemapConfig{Default: "A", Sources: []BasemapSource{
		{Key: "A", Label: "内网A", URL: "http://tiles.internal/{z}/{x}/{y}.png"},
	}}
	body, _ := json.Marshal(custom)
	request := httptest.NewRequest(http.MethodPut, "/api/v1/admin/ops/basemap-config", strings.NewReader(string(body)))
	request.Header.Set("Authorization", "Bearer admin")
	request.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("put status = %d: %s", recorder.Code, recorder.Body.String())
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal("config file not persisted")
	}

	// The saved config drives both the admin view and the public summary.
	adminGet := httptest.NewRequest(http.MethodGet, "/api/v1/admin/ops/basemap-config", nil)
	adminGet.Header.Set("Authorization", "Bearer admin")
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, adminGet)
	if !strings.Contains(recorder.Body.String(), "tiles.internal") {
		t.Fatalf("admin get should expose URLs: %s", recorder.Body.String())
	}

	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, radarWorkspacePrefix+"basemap-config", nil))
	if !strings.Contains(recorder.Body.String(), `"default":"A"`) {
		t.Fatalf("public get should reflect saved config: %s", recorder.Body.String())
	}

	// Invalid configs are rejected without touching the stored file.
	for _, invalid := range []BasemapConfig{
		{Default: "A", Sources: nil},
		{Default: "missing", Sources: custom.Sources},
		{Default: "A", Sources: []BasemapSource{{Key: "A", Label: "A", URL: "ftp://nope/{z}/{x}/{y}.png"}}},
		{Default: "A", Sources: []BasemapSource{{Key: "A", Label: "A", URL: "http://t/{z}/{x}.png"}}},
		{Default: "A", Sources: []BasemapSource{{Key: "bad key", Label: "A", URL: "http://t/{z}/{x}/{y}.png"}}},
		{Default: "A", Sources: []BasemapSource{{Key: "A", Label: "A", URL: "http://t/{z}/{x}/{y}.png"}, {Key: "A", Label: "B", URL: "http://t/{z}/{x}/{y}.png"}}},
	} {
		payload, _ := json.Marshal(invalid)
		req := httptest.NewRequest(http.MethodPut, "/api/v1/admin/ops/basemap-config", strings.NewReader(string(payload)))
		req.Header.Set("Authorization", "Bearer admin")
		req.Header.Set("Content-Type", "application/json")
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, req)
		if recorder.Code != http.StatusBadRequest && recorder.Code != http.StatusUnprocessableEntity {
			t.Fatalf("invalid config %+v status = %d, want 400/422", invalid, recorder.Code)
		}
	}
}

func TestBasemapConfigRequiresAdminAuth(t *testing.T) {
	handler := basemapConfigHandler(t, t.TempDir()+"/missing.json")
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/ops/basemap-config", nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated admin status = %d, want 401", recorder.Code)
	}
}

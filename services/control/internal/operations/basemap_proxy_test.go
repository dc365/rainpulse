package operations

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// basemapTestHandler serves a two-source config (an intranet-style file server
// and a token-protected template) backed by a real config file in t.TempDir().
func basemapTestHandler(t *testing.T, upstreamURL, token string) http.Handler {
	t.Helper()
	return NewHandler(&Service{}, http.NotFoundHandler(), HTTPOptions{
		AdminToken:        "admin",
		TiandituToken:     token,
		BasemapConfigPath: writeBasemapTestConfig(t, upstreamURL),
	})
}

func writeBasemapTestConfig(t *testing.T, upstreamURL string) string {
	t.Helper()
	config := BasemapConfig{
		Default: "XZ",
		Sources: []BasemapSource{
			{Key: "XZ", Label: "行政", URL: upstreamURL + "/xingzheng/{z}/{x}/{y}.png"},
			{Key: "TDT", Label: "天地图", URL: "https://t{0-7}.tianditu.gov.cn/DataServer?T=vec_w&x={x}&y={y}&l={z}&tk={token}", Overlay: "https://t{0-7}.tianditu.gov.cn/DataServer?T=cva_w&x={x}&y={y}&l={z}&tk={token}"},
		},
	}
	return writeBasemapConfigFile(t, config)
}

func writeBasemapConfigFile(t *testing.T, config BasemapConfig) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "basemap-config.json")
	data, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestBasemapTileProxyPassesLayerCoordinatesAndToken(t *testing.T) {
	var gotPath, gotQuery string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotQuery = r.URL.Path, r.URL.RawQuery
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write([]byte{0x89, 'P', 'N', 'G', 1, 2, 3})
	}))
	defer upstream.Close()

	handler := basemapTestHandler(t, upstream.URL, "tok-1")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, basemapTilesPrefix+"XZ/7/105/53.png", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body %s", recorder.Code, recorder.Body.String())
	}
	if gotPath != "/xingzheng/7/105/53.png" {
		t.Fatalf("upstream path = %q", gotPath)
	}
	if gotQuery != "" {
		t.Fatalf("intranet template should not leak query, got %q", gotQuery)
	}
	if got := recorder.Header().Get("Content-Type"); got != "image/png" {
		t.Fatalf("content-type = %q", got)
	}
	if got := recorder.Header().Get("Cache-Control"); !strings.Contains(got, "max-age") {
		t.Fatalf("cache-control = %q, want caching", got)
	}
	if body := recorder.Body.String(); body != "\x89PNG\x01\x02\x03" {
		t.Fatalf("body = %q, want passthrough", body)
	}
}

func TestBasemapTileProxyValidatesRequests(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write([]byte("png"))
	}))
	defer upstream.Close()

	handler := basemapTestHandler(t, upstream.URL, "tok-1")
	for _, path := range []string{
		basemapTilesPrefix + "osm/7/105/53.png",
		basemapTilesPrefix + "XZ/0/0/0.png",
		basemapTilesPrefix + "XZ/19/0/0.png",
		basemapTilesPrefix + "XZ/7/200/53.png",
		basemapTilesPrefix + "XZ/7/105/53",
		basemapTilesPrefix + "XZ-annot/7/105/53.png", // XZ has no overlay
	} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusBadRequest && recorder.Code != http.StatusNotFound {
			t.Fatalf("%s status = %d, want 400/404", path, recorder.Code)
		}
	}

	// Tianditu source without a server token stays explicitly unavailable.
	noToken := basemapTestHandler(t, upstream.URL, " ")
	recorder := httptest.NewRecorder()
	noToken.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, basemapTilesPrefix+"TDT/7/105/53.png", nil))
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("missing token status = %d, want 503", recorder.Code)
	}

	badUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "denied", http.StatusForbidden)
	}))
	defer badUpstream.Close()
	failing := basemapTestHandler(t, badUpstream.URL, "tok-1")
	recorder = httptest.NewRecorder()
	failing.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, basemapTilesPrefix+"XZ/7/105/53.png", nil))
	if recorder.Code != http.StatusBadGateway {
		t.Fatalf("upstream 403 status = %d, want 502", recorder.Code)
	}
}

func TestBasemapTileProxyLegacyTiandituKeysKeepWorking(t *testing.T) {
	// Legacy keys must resolve to a {token} template even without network:
	// a missing token then yields 503 rather than 404, proving the alias
	// mapping without any live upstream call.
	noToken := basemapTestHandler(t, "http://127.0.0.1:1", " ")
	for _, key := range []string{"vec", "cva", "img", "cia"} {
		recorder := httptest.NewRecorder()
		noToken.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, basemapTilesPrefix+key+"/7/105/53.png", nil))
		if recorder.Code != http.StatusServiceUnavailable {
			t.Fatalf("legacy %s status = %d, want 503 (alias resolved, token missing)", key, recorder.Code)
		}
	}
}

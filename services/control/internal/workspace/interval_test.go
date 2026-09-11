package workspace

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCurrentNumericalSidecarAliases(t *testing.T) {
	asset := "/api/v1/products/11111111-1111-1111-1111-111111111111/assets/22222222-2222-2222-2222-222222222222/content"
	if !productAssetPattern.MatchString(asset) {
		t.Fatal("published content URL must support exact sampling")
	}
	metadata := pointQueryMetadata{ObjectPath: "query/point-index.bin", SizeBytes: 69, SHA256: strings.Repeat("a", 64), LeadMinutes: []int{5}, ValidTimes: []string{"2026-08-28T08:35:00Z"}}
	data, _ := json.Marshal(fileProductManifest{PointQueries: map[string]pointQueryMetadata{"nowcastnet": metadata}})
	actual, err := pointQueryFromManifest(data, nowcastPointQueryID)
	if err != nil || actual.ObjectPath != metadata.ObjectPath {
		t.Fatalf("current NowcastNet sidecar: %v", err)
	}
}

func TestIntervalRejectsInvalidRequestsBeforeResolvingSources(t *testing.T) {
	handler := &runtimeHandler{}
	for _, body := range []string{
		`{}`, `{"cycle_id":"a","start_minutes":0,"end_minutes":0}`,
		`{"cycle_id":"a","algorithm":"lk","start_minutes":1,"end_minutes":60}`,
		`{"cycle_id":"a","algorithm":"lk","start_minutes":0,"end_minutes":125}`,
		`{"cycle_id":"../private","algorithm":"lk","start_minutes":0,"end_minutes":60}`,
		`{"cycle_id":"a","algorithm":"unknown","start_minutes":0,"end_minutes":60}`,
		`{"cycle_id":"a","algorithm":"lk","start_minutes":0,"end_minutes":60,"uri":"s3://private"}`,
	} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodPost, intervalPath, strings.NewReader(body)))
		if response.Code != 400 {
			t.Fatalf("%s: got %d", body, response.Code)
		}
	}
}

func TestIntervalImageAndSampleProxyUseNoStore(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(410)
		_, _ = w.Write([]byte(`{"error":"expired"}`))
	}))
	defer upstream.Close()
	t.Setenv("RAINPULSE_INTERVAL_WORKER_URL", upstream.URL)
	handler := &runtimeHandler{}
	asset := intervalPath + "/" + strings.Repeat("a", 64) + "/image"
	for _, target := range []string{asset, workspaceSamplePath + "?asset_url=" + asset + "&longitude=120&latitude=26"} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest("GET", target, nil))
		if response.Code != 410 || response.Header().Get("Cache-Control") != "no-store" {
			t.Fatalf("unexpected proxy response %d", response.Code)
		}
	}
}

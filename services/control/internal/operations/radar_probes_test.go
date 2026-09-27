package operations

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestBatchProbesIsolateMissingSourceAndPreserveOrder(t *testing.T) {
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var q radarProbeSelection
		_ = json.NewDecoder(r.Body).Decode(&q)
		if q.Asset == "missing" {
			w.WriteHeader(404)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "available", "values": map[string]any{"DBZH_RAW": -8.25}})
	})
	h := NewHandler(&Service{}, next, HTTPOptions{})
	out := httptest.NewRecorder()
	h.ServeHTTP(out, httptest.NewRequest("POST", "/api/v1/workspace/radar-layer-probes", bytes.NewBufferString(`{"layers":[{"id":"s1","asset_url":"ok","x":0.5,"y":0.5},{"id":"s2","asset_url":"missing","x":0.5,"y":0.5}]}`)))
	var got struct {
		Items []struct {
			ID, Status string
			Values     map[string]float64
		}
	}
	if out.Code != 200 || json.Unmarshal(out.Body.Bytes(), &got) != nil || len(got.Items) != 2 {
		t.Fatal(out.Body.String())
	}
	if got.Items[0].ID != "s1" || got.Items[0].Values["DBZH_RAW"] != -8.25 || got.Items[1].Status != "unavailable" {
		t.Fatal(got)
	}
}
func TestBatchLayersRejectEmptyRequest(t *testing.T) {
	h := NewHandler(&Service{}, http.NotFoundHandler(), HTTPOptions{})
	for _, path := range []string{"radar-layer-probes", "radar-layer-resolutions"} {
		out := httptest.NewRecorder()
		h.ServeHTTP(out, httptest.NewRequest("POST", "/api/v1/workspace/"+path, bytes.NewBufferString(`{}`)))
		if out.Code != 422 {
			t.Fatalf("%s: %d", path, out.Code)
		}
	}
}

package operations

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCompositeProbeBindsLocalProductSource(t *testing.T) {
	sources := json.RawMessage(`[{"index":0,"radar_id":"zf101","band":"X","scan_id":"x-scan","sweep_number":2,"asset_sha256":"frozen","qc_identity":{"parameters_sha256":"actual"},"input_uri":"private-location"}]`)
	probe := func(value any) map[string]any {
		return map[string]any{"status": "available", "values": map[string]any{"WINNER_SOURCE": value, "CR_DBZH": 35.0}}
	}
	good := probe(0.0)
	if err := bindCompositeProbeSource(good, "x_only", sources); err != nil {
		t.Fatal(err)
	}
	winning := good["winning_source"].(map[string]any)
	if winning["radar_id"] != "zf101" || winning["scan_id"] != "x-scan" || winning["input_uri"] != nil {
		t.Fatal(winning)
	}
	if winning["qc_identity"].(map[string]any)["parameters_sha256"] != "actual" {
		t.Fatal(winning)
	}
	for _, value := range []any{0.5, 1.0, math.NaN(), math.Inf(1)} {
		if bindCompositeProbeSource(probe(value), "x_only", sources) == nil {
			t.Fatalf("accepted invalid index %v", value)
		}
	}
	if bindCompositeProbeSource(probe(0.0), "s_only", sources) == nil {
		t.Fatal("S probe accepted X source")
	}
	mismatchedSweep := probe(0.0)
	mismatchedSweep["values"].(map[string]any)["WINNER_SWEEP_NUMBER"] = 3.0
	if bindCompositeProbeSource(mismatchedSweep, "x_only", sources) == nil {
		t.Fatal("accepted source and numeric sweep contradiction")
	}
	duplicates := json.RawMessage(`[{"index":0,"band":"X"},{"index":0,"band":"X"}]`)
	if bindCompositeProbeSource(probe(0.0), "x_only", duplicates) == nil {
		t.Fatal("accepted ambiguous source")
	}
	noWinner := probe(-1.0)
	if bindCompositeProbeSource(noWinner, "sx_composite", sources) != nil || noWinner["winning_source"] != nil {
		t.Fatal(noWinner)
	}
	legacy := probe(0.0)
	if bindCompositeProbeSource(legacy, "sx_composite", nil) != nil || legacy["source_identity_status"] != "unreported" {
		t.Fatal(legacy)
	}
}

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

func TestFullNetworkProbeLimit(t *testing.T) {
	h := NewHandler(&Service{}, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"status":"available"}`)) }), HTTPOptions{})
	for _, n := range []int{32, 33} {
		layers := make([]radarProbeSelection, n)
		for i := range layers {
			layers[i] = radarProbeSelection{ID: fmt.Sprint(i), Asset: "ok", X: 0.5, Y: 0.5}
		}
		body, _ := json.Marshal(map[string]any{"layers": layers})
		out := httptest.NewRecorder()
		h.ServeHTTP(out, httptest.NewRequest("POST", "/api/v1/workspace/radar-layer-probes", bytes.NewReader(body)))
		want := 200
		if n == 33 {
			want = 422
		}
		if out.Code != want {
			t.Fatalf("%d layers: got %d want %d", n, out.Code, want)
		}
		if n == 32 {
			var result struct{ Items []map[string]any }
			if err := json.Unmarshal(out.Body.Bytes(), &result); err != nil || len(result.Items) != 32 {
				t.Fatal(out.Body.String())
			}
		}
	}
}
func TestResolutionLimitAdmitsFullNetworkBeforeDateValidation(t *testing.T) {
	h := NewHandler(&Service{}, http.NotFoundHandler(), HTTPOptions{})
	for _, n := range []int{32, 33} {
		body, _ := json.Marshal(map[string]any{"time": "2026-08-28T00:06:00Z", "day": "invalid", "radar_ids": make([]string, n)})
		out := httptest.NewRecorder()
		h.ServeHTTP(out, httptest.NewRequest("POST", "/api/v1/workspace/radar-layer-resolutions", bytes.NewReader(body)))
		if out.Code != 422 {
			t.Fatal(out.Code)
		}
		hasDateError := bytes.Contains(out.Body.Bytes(), []byte("资料日期非法"))
		if hasDateError != (n == 32) {
			t.Fatalf("%d: %s", n, out.Body.String())
		}
	}
}

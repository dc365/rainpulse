package operations

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRadarManifestPreservesNativeSweepIdentity(t *testing.T) {
	raw := []byte(`{"radar_id":"zf505","scan_id":"12345678-1234-1234-1234-123456789abc","volume_start":"2026-08-28T00:00:38Z","volume_end":"2026-08-28T00:03:20Z","candidate_only":true,"operational_eligible":false,"comparison":{"sweeps":[{"sweep_number":4,"sequence":2,"elevation_deg":0.5,"raw":"sweeps/4/raw.png","qc":"sweeps/4/qc.png","flags":"sweeps/4/flags.png"}]}}`)
	m, e := parseRadarManifest(raw, "zf505", "12345678-1234-1234-1234-123456789abc")
	if e != nil || len(m.Comparison.Sweeps) != 1 || m.Comparison.Sweeps[0].Number != 4 {
		t.Fatalf("%+v %v", m, e)
	}
	var bad map[string]any
	_ = json.Unmarshal(raw, &bad)
	bad["radar_id"] = "zf101"
	if _, e = parseRadarManifest(JSON(bad), "zf505", "12345678-1234-1234-1234-123456789abc"); e == nil {
		t.Fatal("wrong station accepted")
	}
	bad["radar_id"] = "zf505"
	bad["comparison"] = map[string]any{"sweeps": []any{map[string]any{"raw": "../raw.png", "qc": "qc.png", "flags": "flags.png"}}}
	if _, e = parseRadarManifest(JSON(bad), "zf505", "12345678-1234-1234-1234-123456789abc"); e == nil {
		t.Fatal("unsafe path accepted")
	}
}
func TestRadarWorkspaceRejectsWritesAndBadIdentityWithoutAdmin(t *testing.T) {
	h := NewHandler(&Service{}, http.NotFoundHandler(), HTTPOptions{})
	for _, p := range []string{"/api/v1/workspace/radar-stations", "/api/v1/workspace/radar-scans", "/api/v1/workspace/radar-products/bad"} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", p, nil))
		if w.Code != 405 {
			t.Fatalf("%s: %d", p, w.Code)
		}
	}
	for _, p := range []string{"/api/v1/workspace/radar-stations?band=Q", "/api/v1/workspace/radar-scans?radar_id=zf101", "/api/v1/workspace/radar-products/bad"} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", p, nil))
		if w.Code != 422 {
			t.Fatalf("%s: %d", p, w.Code)
		}
	}
}

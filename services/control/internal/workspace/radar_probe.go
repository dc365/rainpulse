package workspace

import (
	"context"
	"encoding/json"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/radarprobe"
	"github.com/google/uuid"
	"net/http"
)

// Internal composition endpoint, read-only and bounded; no arbitrary object URI.
func (h *runtimeHandler) radarSourceProbe(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" {
		http.Error(w, "method not allowed", 405)
		return
	}
	var q struct {
		Asset string  `json:"asset_url"`
		X     float64 `json:"x"`
		Y     float64 `json:"y"`
	}
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&q) != nil {
		http.Error(w, "invalid query", 400)
		return
	}
	match := diagnosticAssetPattern.FindStringSubmatch(q.Asset)
	if match == nil || h.store == nil || h.objects == nil {
		http.Error(w, "unsupported source", 404)
		return
	}
	id, err := uuid.Parse(match[1])
	if err != nil {
		http.Error(w, "invalid identity", 400)
		return
	}
	record, err := h.store.GetAnalysisDiagnosticsByJob(r.Context(), id)
	if err != nil {
		http.Error(w, "source unavailable", 404)
		return
	}
	raw, _, err := h.objects.Read(r.Context(), record.BundleURI, "manifest.json")
	if err != nil {
		http.Error(w, "manifest unavailable", 404)
		return
	}
	var manifest struct {
		Layers []struct {
			ID    string           `json:"layer_id"`
			Probe radarprobe.Index `json:"probe"`
		} `json:"layers"`
	}
	if json.Unmarshal(raw, &manifest) != nil {
		http.Error(w, "invalid manifest", 422)
		return
	}
	for _, layer := range manifest.Layers {
		if layer.ID == match[2] {
			result, e := radarprobe.Sample(r.Context(), layer.Probe, q.X, q.Y, func(ctx context.Context, key string) ([]byte, error) {
				data, _, e := h.objects.Read(ctx, record.BundleURI, key)
				return data, e
			})
			if e != nil {
				_ = json.NewEncoder(w).Encode(map[string]any{"status": "unavailable", "reason": "图件没有有效数值索引，请重新生成"})
				return
			}
			_ = json.NewEncoder(w).Encode(result)
			return
		}
	}
	http.Error(w, "layer unavailable", 404)
}

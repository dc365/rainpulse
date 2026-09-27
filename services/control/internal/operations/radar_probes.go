package operations

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/radarprobe"
	"net/http"
	"net/http/httptest"
	"strings"
	"time"
)

type radarProbeSelection struct {
	ProductID string  `json:"product_id"`
	ID        string  `json:"id"`
	Asset     string  `json:"asset_url"`
	ResultID  string  `json:"result_id"`
	Sweep     int     `json:"sweep_number"`
	X         float64 `json:"x"`
	Y         float64 `json:"y"`
}

func (h *Handler) radarProbes(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" {
		writeProblem(w, problem(405, "method_not_allowed", "只提供点查"))
		return
	}
	var q struct {
		Layers []radarProbeSelection `json:"layers"`
	}
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 32768)).Decode(&q) != nil || len(q.Layers) < 1 || len(q.Layers) > 16 {
		writeProblem(w, Invalid("点查最多16个图层"))
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	items := make([]map[string]any, len(q.Layers))
	jobs := make(chan int, len(items))
	done := make(chan struct{}, 4)
	for i := range items {
		jobs <- i
	}
	close(jobs)
	for worker := 0; worker < 4; worker++ {
		go func() {
			defer func() { done <- struct{}{} }()
			for i := range jobs {
				layer := q.Layers[i]
				result, err := h.radarProbeOne(ctx, layer)
				if err != nil {
					result = map[string]any{"status": "unavailable", "reason": "该结果无有效数值索引，需重新生成"}
				}
				result["id"] = layer.ID
				items[i] = result
			}
		}()
	}
	for worker := 0; worker < 4; worker++ {
		<-done
	}
	_ = json.NewEncoder(w).Encode(map[string]any{"items": items})
}
func (h *Handler) radarProbeOne(ctx context.Context, q radarProbeSelection) (map[string]any, error) {
	if q.ResultID == "" {
		body, _ := json.Marshal(q)
		req, _ := http.NewRequestWithContext(ctx, "POST", "/api/v1/workspace/radar-source-probe", bytes.NewReader(body))
		response := httptest.NewRecorder()
		h.next.ServeHTTP(response, req)
		var result map[string]any
		if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &result) != nil || result == nil {
			return nil, ErrNotFound
		}
		return result, nil
	}
	ids := strings.Split(q.ResultID, ".")
	if len(ids) != 2 || !ValidID(ids[0]) || !ValidID(ids[1]) {
		return nil, Invalid("结果身份非法")
	}
	task, err := h.service.Store.Task(ctx, ids[0])
	if err != nil {
		return nil, err
	}
	if task.State != "SUCCEEDED" || task.CurrentAttempt != ids[1] || task.Spec.Kind != "multiband" {
		return nil, ErrNotFound
	}
	raw, _, err := h.service.Asset(ctx, task.ID, "manifest.json")
	if err != nil {
		return nil, err
	}
	if q.ProductID != "" {
		m, e := parseCompositeManifest(raw)
		if e != nil {
			return nil, e
		}
		for _, p := range m.Comparison.Products {
			if p.ProductID == q.ProductID && p.Map != nil {
				return radarprobe.Sample(ctx, p.Map.Probe, q.X, q.Y, func(ctx context.Context, key string) ([]byte, error) {
					data, _, e := h.service.Asset(ctx, task.ID, key)
					return data, e
				})
			}
		}
		return nil, ErrNotFound
	}
	var manifest struct {
		Contract   string `json:"contract"`
		Comparison struct {
			Sweeps []struct {
				Number int `json:"sweep_number"`
				Map    struct {
					Probe radarprobe.Index `json:"probe"`
				} `json:"map"`
			} `json:"sweeps"`
		} `json:"comparison"`
	}
	if json.Unmarshal(raw, &manifest) != nil || manifest.Contract != "rainpulse.multiband.x-qc-preview-v1" {
		return nil, ErrNotFound
	}
	for _, sweep := range manifest.Comparison.Sweeps {
		if sweep.Number == q.Sweep {
			return radarprobe.Sample(ctx, sweep.Map.Probe, q.X, q.Y, func(ctx context.Context, key string) ([]byte, error) {
				data, _, e := h.service.Asset(ctx, task.ID, key)
				return data, e
			})
		}
	}
	return nil, ErrNotFound
}

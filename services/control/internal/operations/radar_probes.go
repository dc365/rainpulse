package operations

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/radarprobe"
	"math"
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
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 32768)).Decode(&q) != nil || len(q.Layers) < 1 || len(q.Layers) > 32 {
		writeProblem(w, Invalid("点查最多32个图层"))
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
				result, sampleErr := radarprobe.Sample(ctx, p.Map.Probe, q.X, q.Y, func(ctx context.Context, key string) ([]byte, error) {
					data, _, e := h.service.Asset(ctx, task.ID, key)
					return data, e
				})
				if sampleErr != nil {
					return nil, sampleErr
				}
				if m.SourceContract == radarprobe.CompositeSourceContract {
					return radarprobe.BindCompositeSource(result, raw, q.ProductID)
				}
				sources := p.Sources
				if q.ProductID == "sx_composite" {
					sources = m.Sources
				}
				if err := bindCompositeProbeSource(result, q.ProductID, sources); err != nil {
					return nil, err
				}
				return result, nil
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

func bindCompositeProbeSource(result map[string]any, product string, raw json.RawMessage) error {
	if result["status"] != "available" || (product != "sx_composite" && product != "s_only" && product != "x_only") {
		return nil
	}
	values, ok := result["values"].(map[string]any)
	if !ok || values["WINNER_SOURCE"] == nil {
		result["source_identity_status"] = "unreported"
		return nil
	}
	index, ok := values["WINNER_SOURCE"].(float64)
	if !ok || math.IsNaN(index) || math.IsInf(index, 0) || math.Trunc(index) != index || index < -1 || index > 65535 {
		return ErrNotFound
	}
	if index == -1 {
		result["source_identity_status"] = "no_winner"
		return nil
	}
	if len(raw) == 0 || string(raw) == "null" {
		result["source_identity_status"] = "unreported"
		return nil
	}
	var sources []map[string]any
	if json.Unmarshal(raw, &sources) != nil {
		return ErrNotFound
	}
	var selected map[string]any
	for _, source := range sources {
		if source["index"] == index {
			if selected != nil {
				return ErrNotFound
			}
			selected = source
		}
	}
	if selected == nil || (product == "s_only" && selected["band"] != "S") || (product == "x_only" && selected["band"] != "X") {
		return ErrNotFound
	}
	if selected["band"] != "S" && selected["band"] != "X" {
		return ErrNotFound
	}
	if sweep := values["WINNER_SWEEP_NUMBER"]; sweep != nil && selected["sweep_number"] != sweep {
		return ErrNotFound
	}
	winning := map[string]any{}
	for _, key := range []string{"index", "radar_id", "band", "scan_id", "sweep_number", "asset_sha256", "qc_version", "qc_identity", "volume_end", "available_at"} {
		if value, exists := selected[key]; exists {
			winning[key] = value
		}
	}
	result["winning_source"] = winning
	result["source_identity_status"] = "recorded"
	return nil
}

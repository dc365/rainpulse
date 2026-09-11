package workspace

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"time"
)

type comparisonInput struct {
	CycleID    string   `json:"cycle_id"`
	Algorithms []string `json:"algorithms"`
	Lead       int      `json:"lead_minutes"`
	PSD        bool     `json:"psd"`
}

func validAlgorithms(names []string) bool {
	if len(names) < 1 || len(names) > 3 {
		return false
	}
	seen := map[string]bool{}
	for _, name := range names {
		if seen[name] || !slices.Contains([]string{"lk", "steps", "nowcastnet"}, name) {
			return false
		}
		seen[name] = true
	}
	return true
}
func validCycleID(id string) bool {
	return id != "" && len(id) <= 256 && !strings.ContainsAny(id, "/?%")
}
func decodeAnalysisInput(w http.ResponseWriter, r *http.Request, target any) bool {
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 65536))
	d.DisallowUnknownFields()
	if d.Decode(target) != nil || d.Decode(new(any)) != io.EOF {
		runtimeWriteError(w, 400, "invalid_analysis", "检验参数格式不正确")
		return false
	}
	return true
}
func analysisJSON(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(data)
}
func (h *runtimeHandler) analysisDetail(ctx context.Context, id string) (cycleDetail, error) {
	r := httptest.NewRecorder()
	h.intervalProjection.ServeHTTP(r, httptest.NewRequest("GET", workspacePrefix+"/"+id, nil).WithContext(ctx))
	var d cycleDetail
	if r.Code != 200 || json.Unmarshal(r.Body.Bytes(), &d) != nil || d.CycleID != id {
		return d, fmt.Errorf("起报数据不可用")
	}
	return d, nil
}
func callAnalysisWorker(ctx context.Context, path string, payload any) (map[string]any, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, "POST", intervalURL()+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := (&http.Client{Timeout: 90 * time.Second}).Do(req)
	if err != nil {
		return nil, fmt.Errorf("检验计算服务超时或不可用")
	}
	defer resp.Body.Close()
	var data map[string]any
	if resp.StatusCode != 200 || json.NewDecoder(io.LimitReader(resp.Body, 2<<20)).Decode(&data) != nil {
		return nil, fmt.Errorf("检验源校验失败或计算服务繁忙")
	}
	return data, nil
}
func (h *runtimeHandler) compareDetail(ctx context.Context, d cycleDetail, input comparisonInput) (map[string]any, error) {
	var truth panelView
	panels := map[string]panelView{}
	for _, p := range d.Panels {
		panels[p.PanelID] = p
		if p.PanelID == "qpe" {
			truth = p
		}
	}
	if !hasNativeLead(truth, input.Lead, true) {
		return nil, fmt.Errorf("该时效无匹配雷达 QPE")
	}
	observed, err := h.intervalSources(ctx, d, truth, input.Lead-5, input.Lead)
	if err != nil {
		return nil, fmt.Errorf("雷达 QPE 数值源不可用")
	}
	forecasts := map[string][]intervalSource{}
	for _, a := range input.Algorithms {
		p := panels[a]
		if !hasNativeLead(p, input.Lead, false) {
			return nil, fmt.Errorf("%s 无原生该时效，整组不参与比较", a)
		}
		sources, err := h.intervalSources(ctx, d, p, input.Lead-5, input.Lead)
		if err != nil {
			return nil, fmt.Errorf("%s 数值源不可用", a)
		}
		forecasts[a] = sources
	}
	return callAnalysisWorker(ctx, "/interval/compare", map[string]any{"truth": observed, "forecasts": forecasts, "lead_minutes": input.Lead, "bounds": d.Grid.RasterBounds, "psd": input.PSD})
}
func (h *runtimeHandler) compareVerification(w http.ResponseWriter, r *http.Request) {
	var input comparisonInput
	if !decodeAnalysisInput(w, r, &input) {
		return
	}
	if !validCycleID(input.CycleID) || !validAlgorithms(input.Algorithms) || input.Lead < 5 || input.Lead > 120 || input.Lead%5 != 0 {
		runtimeWriteError(w, 400, "invalid_analysis", "请选择有效周期、算法和时效")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 120*time.Second)
	defer cancel()
	result := map[string]any{"cycle_id": input.CycleID, "algorithms": input.Algorithms, "lead_minutes": input.Lead, "status": "unavailable"}
	detail, err := h.analysisDetail(ctx, input.CycleID)
	if err == nil {
		var data map[string]any
		data, err = h.compareDetail(ctx, detail, input)
		if err == nil {
			for k, v := range data {
				result[k] = v
			}
			result["status"] = "ready"
		}
	}
	if err != nil {
		result["reason"] = err.Error()
	}
	analysisJSON(w, 200, result)
}

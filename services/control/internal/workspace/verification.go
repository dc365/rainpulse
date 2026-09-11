package workspace

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"time"
)

const verificationPath = "/api/v1/workspace/verification"

func hasNativeLead(p panelView, lead int, observation bool) bool {
	for _, f := range p.Frames {
		if f.LeadMinutes == lead && !f.ReferenceObservation && (f.FrameKind == "native" || (observation && f.FrameKind == "analysis")) {
			return true
		}
	}
	return false
}

func (h *runtimeHandler) calculateVerification(w http.ResponseWriter, r *http.Request) {
	var input struct {
		CycleID   string `json:"cycle_id"`
		Algorithm string `json:"algorithm"`
		Lead      int    `json:"lead_minutes"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2048))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&input) != nil || input.CycleID == "" || len(input.CycleID) > 256 || strings.ContainsAny(input.CycleID, "/?%") || input.Lead < 5 || input.Lead > 120 || input.Lead%5 != 0 || (input.Algorithm != "lk" && input.Algorithm != "steps" && input.Algorithm != "nowcastnet") {
		runtimeWriteError(w, 400, "invalid_verification", "请选择有效起报、算法和原生预报时效")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 120*time.Second)
	defer cancel()
	result := map[string]any{"cycle_id": input.CycleID, "algorithm": input.Algorithm, "lead_minutes": input.Lead, "status": "unavailable", "reason": "该时效缺少匹配的原生预报或雷达 QPE"}
	finish := func() {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		_ = json.NewEncoder(w).Encode(result)
	}
	recorder := httptest.NewRecorder()
	h.intervalProjection.ServeHTTP(recorder, httptest.NewRequest("GET", workspacePrefix+"/"+input.CycleID, nil).WithContext(ctx))
	var detail cycleDetail
	if recorder.Code != 200 || json.Unmarshal(recorder.Body.Bytes(), &detail) != nil {
		finish()
		return
	}
	var truth, forecast panelView
	for _, p := range detail.Panels {
		if p.PanelID == "qpe" {
			truth = p
		}
		if p.PanelID == input.Algorithm {
			forecast = p
		}
	}
	if !hasNativeLead(truth, input.Lead, true) || !hasNativeLead(forecast, input.Lead, false) {
		finish()
		return
	}
	observed, err := h.intervalSources(ctx, detail, truth, input.Lead-5, input.Lead)
	if err != nil {
		result["reason"] = "雷达 QPE 数值源不可用"
		finish()
		return
	}
	predicted, err := h.intervalSources(ctx, detail, forecast, input.Lead-5, input.Lead)
	if err != nil {
		result["reason"] = "预报数值源不可用"
		finish()
		return
	}
	payload, _ := json.Marshal(map[string]any{"algorithm": input.Algorithm, "lead_minutes": input.Lead, "truth": observed, "forecast": predicted, "bounds": detail.Grid.RasterBounds})
	request, _ := http.NewRequestWithContext(ctx, "POST", intervalURL()+"/interval/verification", bytes.NewReader(payload))
	request.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: 90 * time.Second}).Do(request)
	if err != nil {
		result["reason"] = "检验计算超时或服务暂不可用，请重试"
		finish()
		return
	}
	defer response.Body.Close()
	var metrics map[string]any
	if response.StatusCode != 200 || json.NewDecoder(io.LimitReader(response.Body, 128<<10)).Decode(&metrics) != nil {
		result["reason"] = "检验未完成：数值源校验失败或服务繁忙，请重试"
		finish()
		return
	}
	result["status"] = "ready"
	delete(result, "reason")
	result["metrics"] = metrics
	finish()
}

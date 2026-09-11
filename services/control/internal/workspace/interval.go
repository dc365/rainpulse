package workspace

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"
)

const intervalPath = "/api/v1/workspace/accumulations"

var intervalAssetPattern = regexp.MustCompile(`^/api/v1/workspace/accumulations/[0-9a-f]{64}/image$`)

type intervalRequest struct {
	Algorithm string `json:"algorithm"`
	CycleID   string `json:"cycle_id"`
	Start     int    `json:"start_minutes"`
	End       int    `json:"end_minutes"`
}
type intervalSource struct {
	URI        string `json:"uri,omitempty"`
	Local      string `json:"local,omitempty"`
	ObjectPath string `json:"object_path,omitempty"`
	SHA256     string `json:"sha256"`
	Leads      []int  `json:"leads,omitempty"`
	Indices    []int  `json:"indices,omitempty"`
}

func intervalURL() string {
	if value := strings.TrimRight(os.Getenv("RAINPULSE_INTERVAL_WORKER_URL"), "/"); value != "" {
		return value
	}
	return "http://product-builder-worker:8091"
}

func (handler *runtimeHandler) proxyInterval(w http.ResponseWriter, r *http.Request, target string) {
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, intervalURL()+target, nil)
	if err != nil {
		runtimeWriteError(w, 400, "invalid_interval", "invalid interval request")
		return
	}
	resp, err := (&http.Client{Timeout: 20 * time.Second}).Do(req)
	if err != nil {
		runtimeWriteError(w, 503, "interval_unavailable", "累计服务暂不可用")
		return
	}
	defer resp.Body.Close()
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, io.LimitReader(resp.Body, 8<<20))
}

func (handler *runtimeHandler) calculateInterval(w http.ResponseWriter, r *http.Request) {
	var input intervalRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2048))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&input) != nil || input.Start < 0 || input.End > 120 || input.Start >= input.End || input.Start%5 != 0 || input.End%5 != 0 || strings.ContainsAny(input.CycleID, "/?%") || len(input.CycleID) > 256 || input.CycleID == "" {
		runtimeWriteError(w, 400, "invalid_interval", "请选择 0–120 分钟内、以 5 分钟对齐的起止区间")
		return
	}
	if input.Algorithm != "qpe" && input.Algorithm != "lk" && input.Algorithm != "steps" && input.Algorithm != "nowcastnet" {
		runtimeWriteError(w, 400, "invalid_algorithm", "请选择 qpe、lk、steps 或 nowcastnet")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 120*time.Second)
	defer cancel()
	// Resolve the current authoritative projection, never a stale persisted response.
	recorder := httptest.NewRecorder()
	projected := httptest.NewRequest(http.MethodGet, workspacePrefix+"/"+input.CycleID, nil).WithContext(ctx)
	handler.intervalProjection.ServeHTTP(recorder, projected)
	var detail cycleDetail
	if recorder.Code != 200 || json.Unmarshal(recorder.Body.Bytes(), &detail) != nil {
		runtimeWriteError(w, 404, "cycle_unavailable", "起报周期不可用")
		return
	}
	panels := []panelView{}
	for _, panel := range detail.Panels {
		if panel.PanelID != input.Algorithm {
			continue
		}
		if panel.PanelID != "qpe" && panel.PanelID != "lk" && panel.PanelID != "steps" && panel.PanelID != "nowcastnet" {
			continue
		}
		sources, err := handler.intervalSources(ctx, detail, panel, input.Start, input.End)
		panel.DataKind, panel.LegendUnit, panel.Legend = "accumulation_interval", "mm", accumulationLegend()
		panel.Frames = []frameView{}
		panel.Status, panel.UnavailableReason = "unavailable", "所选区间缺少完整数值数据，无法累计"
		if err == nil {
			payload, _ := json.Marshal(map[string]any{"algorithm": panel.PanelID, "start": input.Start, "end": input.End, "issue_time": detail.IssueTime, "sources": sources, "bounds": detail.Grid.RasterBounds})
			request, _ := http.NewRequestWithContext(ctx, http.MethodPost, intervalURL()+"/interval", bytes.NewReader(payload))
			request.Header.Set("Content-Type", "application/json")
			response, callErr := (&http.Client{Timeout: 90 * time.Second}).Do(request)
			if callErr == nil {
				var frame frameView
				decodeErr := json.NewDecoder(io.LimitReader(response.Body, 65536)).Decode(&frame)
				response.Body.Close()
				if response.StatusCode == 200 && decodeErr == nil && frame.Unit == "mm" && intervalAssetPattern.MatchString(frame.ImageURL) {
					panel.Frames = []frameView{frame}
					panel.Status, panel.UnavailableReason = "ready", ""
				} else {
					panel.UnavailableReason = "累计未完成：源数据不可用或计算服务繁忙，请重试"
				}
			} else {
				panel.UnavailableReason = "累计请求超时或服务暂不可用，请重试"
			}
		}
		panels = append(panels, panel)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(map[string]any{"cycle_id": input.CycleID, "start_minutes": input.Start, "end_minutes": input.End, "panels": panels})
}

func (handler *runtimeHandler) intervalSources(ctx context.Context, detail cycleDetail, panel panelView, start, end int) ([]intervalSource, error) {
	fail := fmt.Errorf("complete numerical sources unavailable")
	frames := []frameView{}
	for lead := start + 5; lead <= end; lead += 5 {
		found := false
		for _, frame := range panel.Frames {
			if frame.LeadMinutes == lead {
				frames = append(frames, frame)
				found = true
				break
			}
		}
		if !found {
			return nil, fail
		}
	}
	if len(frames) == 0 || handler.store == nil || handler.objects == nil {
		return nil, fail
	}
	if panel.PanelID == "qpe" {
		sources := []intervalSource{}
		for _, frame := range frames {
			match := diagnosticAssetPattern.FindStringSubmatch(frame.ImageURL)
			if match == nil {
				return nil, fail
			}
			id, err := uuid.Parse(match[1])
			if err != nil {
				return nil, err
			}
			diagnostics, err := handler.store.GetAnalysisDiagnosticsByJob(ctx, id)
			if err != nil {
				return nil, err
			}
			data, _, err := handler.objects.Read(ctx, diagnostics.BundleURI, "manifest.json")
			if err != nil {
				return nil, err
			}
			metadata, err := pointQueryFromManifest(data, qpePointQueryID)
			if err != nil {
				return nil, err
			}
			sources = append(sources, intervalSource{URI: diagnostics.BundleURI, ObjectPath: metadata.ObjectPath, SHA256: metadata.SHA256, Leads: []int{frame.LeadMinutes}, Indices: []int{0}})
		}
		return sources, nil
	}
	if panel.PanelID == "lk" {
		match := productAssetPattern.FindStringSubmatch(strings.TrimSuffix(frames[0].ImageURL, "/content"))
		if match == nil {
			return nil, fail
		}
		id, _ := uuid.Parse(match[1])
		product, err := handler.store.GetProduct(ctx, id)
		if err != nil {
			return nil, err
		}
		assets, err := handler.store.ListProductAssets(ctx, id)
		if err != nil {
			return nil, err
		}
		for _, asset := range assets {
			if asset.AssetType == "point_query_index" {
				source := intervalSource{URI: asset.ObjectURI, SHA256: asset.SHA256}
				issue, err := time.Parse(time.RFC3339, detail.IssueTime)
				if err != nil {
					return nil, err
				}
				for index, valid := range product.ValidTimes {
					lead := int(valid.Sub(issue) / time.Minute)
					if lead > start && lead <= end {
						source.Leads = append(source.Leads, lead)
						source.Indices = append(source.Indices, index)
					}
				}
				if len(source.Leads) != len(frames) {
					return nil, fail
				}
				return []intervalSource{source}, nil
			}
		}
		return nil, fail
	}
	if panel.PanelID == "steps" {
		id, err := uuid.Parse(detail.EnsembleID)
		if err != nil {
			return nil, err
		}
		data, err := os.ReadFile(filepath.Join(handler.ensembleRoot, id.String(), "manifest.json"))
		if err != nil {
			return nil, err
		}
		var manifest struct {
			Source struct {
				URI    string `json:"uri"`
				SHA256 string `json:"sha256"`
			} `json:"source_forecast"`
		}
		if json.Unmarshal(data, &manifest) != nil || manifest.Source.URI == "" {
			return nil, fail
		}
		return []intervalSource{{URI: manifest.Source.URI, SHA256: manifest.Source.SHA256}}, nil
	}
	id, err := uuid.Parse(detail.NowcastNetID)
	if err != nil {
		return nil, err
	}
	data, _, err := handler.nowcastNetProducts.ReadObject(ctx, id.String(), "manifest.json")
	if err != nil {
		return nil, err
	}
	metadata, err := pointQueryFromManifest(data, nowcastPointQueryID)
	if err != nil {
		metadata, err = pointQueryFromManifest(data, "nowcastnet")
	}
	if err != nil {
		return nil, err
	}
	source := intervalSource{Local: id.String(), ObjectPath: metadata.ObjectPath, SHA256: metadata.SHA256}
	if store, ok := handler.store.(NowcastNetAlgorithmRunStore); ok {
		runs, err := store.ListCompletedNowcastNetAlgorithmRuns(ctx, 500)
		if err != nil {
			return nil, err
		}
		for _, run := range runs {
			if run.JobID == id {
				source.URI, source.Local = run.OutputURI, ""
				break
			}
		}
	}
	for index, lead := range metadata.LeadMinutes {
		if lead > start && lead <= end {
			source.Leads = append(source.Leads, lead)
			source.Indices = append(source.Indices, index)
		}
	}
	if len(source.Leads) != len(frames) {
		return nil, fail
	}
	return []intervalSource{source}, nil
}

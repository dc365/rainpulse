package operations

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"
)

type layerScan struct {
	ScanID  string    `json:"scan_id"`
	RadarID string    `json:"radar_id"`
	Start   time.Time `json:"volume_start"`
	End     time.Time `json:"volume_end"`
	Results []struct {
		ID string `json:"result_id"`
	} `json:"results"`
}

func (h *Handler) radarResolutions(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" {
		writeProblem(w, problem(405, "method_not_allowed", "只提供图层解析"))
		return
	}
	var q struct {
		Time   time.Time `json:"time"`
		Day    string    `json:"day"`
		Radars []string  `json:"radar_ids"`
		Cycle  string    `json:"cycle_id"`
	}
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384)).Decode(&q) != nil || q.Time.IsZero() || len(q.Radars) > 32 || len(q.Radars) == 0 {
		writeProblem(w, Invalid("最多解析32站，请指定时次"))
		return
	}
	start, e := time.Parse("2006-01-02T15:04:05Z07:00", q.Day+"T00:00:00+08:00")
	if e != nil {
		writeProblem(w, Invalid("资料日期非法"))
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()
	window := "&start=" + url.QueryEscape(start.UTC().Format(time.RFC3339)) + "&end=" + url.QueryEscape(start.Add(24*time.Hour).UTC().Format(time.RFC3339))
	catalogReq, _ := http.NewRequestWithContext(ctx, "GET", radarWorkspacePrefix+"radar-stations?band=all"+window, nil)
	catalog, e := h.radarStations(catalogReq)
	if e != nil {
		writeProblem(w, e)
		return
	}
	raw, _ := json.Marshal(catalog)
	var stations struct {
		Items []struct {
			ID   string `json:"radar_id"`
			Band string `json:"band"`
		} `json:"items"`
	}
	_ = json.Unmarshal(raw, &stations)
	bands := map[string]string{}
	for _, station := range stations.Items {
		bands[station.ID] = station.Band
	}
	items := make([]map[string]any, len(q.Radars))
	var mu sync.Mutex
	times := map[string]bool{}
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
				id := strings.ToLower(q.Radars[i])
				item := map[string]any{"id": id, "sweeps": []any{}}
				items[i] = item
				if bands[id] == "S" {
					item["band"] = "S"
					if q.Cycle == "" {
						item["error"] = "请选择 S 分析周期"
						continue
					}
					req, _ := http.NewRequestWithContext(ctx, "GET", radarWorkspacePrefix+"cycles/"+url.PathEscape(q.Cycle), nil)
					out := httptest.NewRecorder()
					h.next.ServeHTTP(out, req)
					var detail struct {
						IssueTime time.Time `json:"issue_time"`
						Panels    []struct {
							RadarID string            `json:"radar_id"`
							Frames  []json.RawMessage `json:"frames"`
							ID      string            `json:"panel_id"`
						} `json:"panels"`
					}
					if out.Code != 200 || json.Unmarshal(out.Body.Bytes(), &detail) != nil {
						item["error"] = "分析周期不可用"
						continue
					}
					if !detail.IssueTime.Equal(q.Time) {
						item["error"] = "分析周期与资料时次不符"
						continue
					}
					panels := map[string]any{}
					for _, p := range detail.Panels {
						if p.RadarID == id {
							panels[p.ID] = p.Frames
						}
					}
					item["panels"] = panels
					continue
				}
				if bands[id] != "X" {
					item["error"] = "未登记站点"
					continue
				}
				item["band"] = "X"
				cursor := ""
				seen := map[string]bool{}
				var selected *layerScan
				for page := 0; page < 100; page++ {
					req, _ := http.NewRequestWithContext(ctx, "GET", radarWorkspacePrefix+"radar-scans?radar_id="+url.QueryEscape(id)+window+"&cursor="+url.QueryEscape(cursor), nil)
					value, err := h.radarScans(req)
					if err != nil {
						item["error"] = "体扫目录读取失败"
						break
					}
					raw, _ := json.Marshal(value)
					var listing struct {
						Items []layerScan `json:"items"`
						Next  string      `json:"next_cursor"`
					}
					if json.Unmarshal(raw, &listing) != nil {
						item["error"] = "目录格式错误"
						break
					}
					for _, scan := range listing.Items {
						t := scan.Start.UTC().Truncate(6 * time.Minute)
						mu.Lock()
						times[t.Format(time.RFC3339)] = true
						mu.Unlock()
						if t.Equal(q.Time.UTC().Truncate(6*time.Minute)) && (selected == nil || scan.Start.Before(selected.Start)) {
							copy := scan
							selected = &copy
						}
					}
					cursor = listing.Next
					if cursor == "" {
						break
					}
					if seen[cursor] {
						item["error"] = "目录分页异常"
						break
					}
					seen[cursor] = true
					if page == 99 {
						item["error"] = "目录超过分页上限，请缩小资料范围"
					}
				}
				if item["error"] != nil {
					continue
				}
				if selected == nil {
					item["error"] = "该窗口无体扫"
					continue
				}
				item["scan"] = selected
				if len(selected.Results) == 0 {
					item["error"] = "尚无质控结果"
					continue
				}
				req, _ := http.NewRequestWithContext(ctx, "GET", radarWorkspacePrefix+"radar-products/"+selected.Results[0].ID, nil)
				value, err := h.radarProduct(httptest.NewRecorder(), req, []string{"radar-products", selected.Results[0].ID})
				if err != nil {
					item["error"] = "质控结果不可用"
					continue
				}
				result := value.(map[string]any)
				if result["radar_id"] != id || result["scan_id"] != selected.ScanID {
					item["error"] = "质控结果身份不符"
					continue
				}
				item["sweeps"] = result["sweeps"]
			}
		}()
	}
	for worker := 0; worker < 4; worker++ {
		<-done
	}
	values := []string{}
	for t := range times {
		values = append(values, t)
	}
	sort.Strings(values)
	_ = json.NewEncoder(w).Encode(map[string]any{"items": items, "times": values, "time": q.Time})
}

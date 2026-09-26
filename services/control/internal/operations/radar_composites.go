package operations

import (
	"encoding/base64"
	"encoding/json"
	"math"
	"net/http"
	"strings"
	"time"
)

type compositeMap struct {
	CRS        string    `json:"crs"`
	Bounds     []float64 `json:"bounds"`
	ObjectPath string    `json:"object_path"`
}
type compositeProduct struct {
	ProductID         string        `json:"product_id"`
	Label             string        `json:"label"`
	Status            string        `json:"status"`
	Reason            *string       `json:"reason"`
	ContributingBands []string      `json:"contributing_bands"`
	Map               *compositeMap `json:"map,omitempty"`
}
type compositeManifest struct {
	Contract     string `json:"contract"`
	AnalysisTime string `json:"analysis_time"`
	GridID       string `json:"grid_id"`
	Method       string `json:"method"`
	NetworkSHA   string `json:"network_sha256"`
	Comparison   struct {
		Group    string             `json:"comparison_group"`
		SameGrid bool               `json:"same_grid"`
		Products []compositeProduct `json:"products"`
	} `json:"comparison"`
}

func parseCompositeManifest(raw []byte) (compositeManifest, error) {
	var m compositeManifest
	if json.Unmarshal(raw, &m) != nil || m.Contract != "rainpulse.multiband.composite-v1" || !m.Comparison.SameGrid {
		return m, Conflict("组合产品清单不匹配")
	}
	for _, p := range m.Comparison.Products {
		if p.Map == nil {
			continue
		}
		g := p.Map
		if g.CRS != "EPSG:4326" || len(g.Bounds) != 4 || !safeKey(g.ObjectPath) || !strings.HasSuffix(g.ObjectPath, ".png") {
			return m, Conflict("组合地图元数据非法")
		}
		for _, v := range g.Bounds {
			if math.IsNaN(v) || math.IsInf(v, 0) {
				return m, Conflict("组合地图边界非法")
			}
		}
		if g.Bounds[0] < -180 || g.Bounds[2] > 180 || g.Bounds[1] < -90 || g.Bounds[3] > 90 || g.Bounds[0] >= g.Bounds[2] || g.Bounds[1] >= g.Bounds[3] {
			return m, Conflict("组合地图边界非法")
		}
	}
	return m, nil
}
func (h *Handler) radarComposites(w http.ResponseWriter, r *http.Request, parts []string) (any, error) {
	if len(parts) == 1 {
		start, end, err := parseWindow(r.URL.Query(), 24*time.Hour)
		if err != nil {
			return nil, err
		}
		rows, err := h.service.Store.DB.QueryContext(r.Context(), `SELECT t.id::text||'.'||t.current_attempt::text,t.spec#>>'{request,payload,analysis_time}' FROM ops_tasks t WHERE t.kind='multiband' AND t.spec#>>'{request,payload,mode}'='sx_composite' AND t.state='SUCCEEDED' AND t.current_attempt IS NOT NULL AND t.result#>>'{asset,uri}' IS NOT NULL AND (t.spec#>>'{request,payload,analysis_time}')::timestamptz >= $1 AND (t.spec#>>'{request,payload,analysis_time}')::timestamptz < $2 AND NOT EXISTS(SELECT 1 FROM ops_retired_runs rr WHERE rr.run_id=t.run_id) ORDER BY t.updated_at DESC,t.id DESC LIMIT 100`, start, end)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		items := []map[string]string{}
		for rows.Next() {
			var id, at string
			if err = rows.Scan(&id, &at); err != nil {
				return nil, err
			}
			items = append(items, map[string]string{"result_id": id, "analysis_time": at})
		}
		return map[string]any{"items": items}, rows.Err()
	}
	if len(parts) != 2 && len(parts) != 4 {
		return nil, ErrNotFound
	}
	ids := strings.Split(parts[1], ".")
	if len(ids) != 2 || !ValidID(ids[0]) || !ValidID(ids[1]) {
		return nil, Invalid("结果身份非法")
	}
	t, err := h.service.Store.Task(r.Context(), ids[0])
	if err != nil {
		return nil, err
	}
	var req struct {
		Payload struct {
			Mode string `json:"mode"`
		} `json:"payload"`
	}
	if json.Unmarshal(t.Spec.Request, &req) != nil || req.Payload.Mode != "sx_composite" || t.Spec.Kind != "multiband" {
		return nil, ErrNotFound
	}
	if t.State != "SUCCEEDED" || t.CurrentAttempt != ids[1] {
		return nil, problem(410, "result_unavailable", "结果已不可用")
	}
	raw, _, err := h.service.Asset(r.Context(), t.ID, "manifest.json")
	if err != nil {
		return nil, err
	}
	m, err := parseCompositeManifest(raw)
	if err != nil {
		return nil, err
	}
	if len(parts) == 4 {
		if parts[2] != "assets" {
			return nil, ErrNotFound
		}
		b, e := base64.RawURLEncoding.DecodeString(parts[3])
		if e != nil {
			return nil, Invalid("图件身份非法")
		}
		allowed := false
		for _, p := range m.Comparison.Products {
			if p.Map != nil && p.Map.ObjectPath == string(b) {
				allowed = true
			}
		}
		if !allowed {
			return nil, ErrNotFound
		}
		data, media, e := h.service.Asset(r.Context(), t.ID, string(b))
		if e != nil {
			return nil, e
		}
		w.Header().Set("Content-Type", media)
		_, _ = w.Write(data)
		return nil, errResponseWritten
	}
	for i := range m.Comparison.Products {
		p := &m.Comparison.Products[i]
		if p.Map != nil {
			p.Map.ObjectPath = radarWorkspacePrefix + "radar-composites/" + parts[1] + "/assets/" + base64.RawURLEncoding.EncodeToString([]byte(p.Map.ObjectPath))
		}
	}
	return map[string]any{"result_id": parts[1], "manifest": m}, nil
}

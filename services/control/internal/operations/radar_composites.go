package operations

import (
	"encoding/base64"
	"encoding/json"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/radarprobe"
	"math"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type compositeMap struct {
	Probe      radarprobe.Index `json:"probe,omitempty"`
	CRS        string           `json:"crs"`
	Bounds     []float64        `json:"bounds"`
	ObjectPath string           `json:"object_path"`
}
type compositeProduct struct {
	Provenance            json.RawMessage `json:"provenance,omitempty"`
	Method                string          `json:"method,omitempty"`
	MethodLabel           string          `json:"method_label,omitempty"`
	Sources               json.RawMessage `json:"sources"`
	ValidEchoCells        int             `json:"valid_echo_cells"`
	EchoContributingBands []string        `json:"echo_contributing_bands"`
	Unit                  string          `json:"unit"`
	Legend                json.RawMessage `json:"legend,omitempty"`
	ProductID             string          `json:"product_id"`
	Label                 string          `json:"label"`
	Status                string          `json:"status"`
	Reason                *string         `json:"reason"`
	ContributingBands     []string        `json:"contributing_bands"`
	Map                   *compositeMap   `json:"map,omitempty"`
}
type compositeManifest struct {
	SourceContract string          `json:"source_contract,omitempty"`
	SourceScope    json.RawMessage `json:"source_scope,omitempty"`
	MethodLabel    string          `json:"method_label,omitempty"`
	MethodKey      string          `json:"method_key,omitempty"`
	Audit          json.RawMessage `json:"audit,omitempty"`
	Experimental   bool            `json:"experimental"`
	DisplayWarning string          `json:"display_warning,omitempty"`
	ValidEchoCells int             `json:"valid_echo_cells"`
	Sources        json.RawMessage `json:"sources"`
	Skipped        json.RawMessage `json:"skipped"`
	Contract       string          `json:"contract"`
	AnalysisTime   string          `json:"analysis_time"`
	GridID         string          `json:"grid_id"`
	Method         string          `json:"method"`
	NetworkSHA     string          `json:"network_sha256"`
	NetworkRelease string          `json:"network_release,omitempty"`
	ProductID      string          `json:"product_id,omitempty"`
	CadenceSeconds int             `json:"cadence_seconds,omitempty"`
	InputCutoff    string          `json:"input_cutoff,omitempty"`
	Comparison     struct {
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
		if r.URL.Query().Get("series_mode") == "1" {
			return h.radarCompositeSeries(r)
		}
		start, end, err := parseWindow(r.URL.Query(), 24*time.Hour)
		if err != nil {
			return nil, err
		}
		series, err := compositeSeriesQuery(r.URL.Query())
		if err != nil {
			return nil, err
		}
		var page json.RawMessage
		err = h.service.Store.DB.QueryRowContext(r.Context(), compositeTimelineSQL, start, end, series).Scan(&page)
		return page, err
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
	at, err := time.Parse(time.RFC3339Nano, m.AnalysisTime)
	if err != nil {
		return nil, Conflict("组合时次非法")
	}
	var seriesID string
	var legacy bool
	err = h.service.Store.DB.QueryRowContext(r.Context(), compositeSeriesEligibleSQL+`SELECT series_id,legacy FROM eligible WHERE result_id=$3 LIMIT 1`, at, at.Add(time.Second), parts[1]).Scan(&seriesID, &legacy)
	if err != nil {
		return nil, err
	}
	return map[string]any{"result_id": parts[1], "manifest": m, "series_id": seriesID, "series_legacy": legacy}, nil
}

func compositeSeriesQuery(q url.Values) (string, error) {
	values := q["series_id"]
	if len(values) > 1 || len(values) == 1 && values[0] != "" && !shaPattern.MatchString(values[0]) {
		return "", Invalid("组合产品系列身份非法")
	}
	return q.Get("series_id"), nil
}

// Frozen S producer policy supplements the fusion worker identity. Incomplete
// historical declarations remain run-isolated and are never filled from current
// settings. Actual asset metadata is checked by the matched compute worker.
const compositeTimelineSQL = `WITH eligible AS (
 SELECT t.id::text||'.'||t.current_attempt::text AS result_id,
 t.spec#>>'{request,payload,analysis_time}' AS analysis_time,
 (t.spec#>>'{request,payload,analysis_time}')::timestamptz AS at,
 t.updated_at, t.id,
 coalesce(t.spec#>>'{request,payload,product_id}','') AS product_id,
 coalesce(t.spec#>>'{identity,versions,network_release}','') AS network_release,
 coalesce(t.spec#>>'{identity,fingerprint}','') AS fingerprint,
 requested.radars AS requested_radars,
 NOT coalesce(t.spec#>>'{identity,fingerprint}' ~ '^[a-f0-9]{64}$',false)
 OR requested.radars='[]'::jsonb
 OR NOT coalesce(t.spec#>'{request,payload,s_qc_policy_complete}'='true'::jsonb
 AND jsonb_typeof(t.spec#>'{request,payload,s_qc_policy}')='object',false) AS legacy,
 encode(sha256(convert_to(jsonb_build_object(
 'version','sx-configured-series-v2',
 'product_id',t.spec#>>'{request,payload,product_id}',
 'identity',t.spec->'identity',
 'network_sha256',t.spec#>>'{request,payload,network_sha256}',
 'execution_sha256',t.spec#>>'{request,payload,execution_sha256}',
 's_qc_policy',t.spec#>'{request,payload,s_qc_policy}',
 'requested_radars',(SELECT coalesce(jsonb_agg(radar ORDER BY radar),'[]'::jsonb)
 FROM jsonb_array_elements_text(requested.radars) radar),
 'legacy_run_id',CASE WHEN t.spec#>>'{identity,fingerprint}' ~ '^[a-f0-9]{64}$'
 AND requested.radars<>'[]'::jsonb
 AND t.spec#>'{request,payload,s_qc_policy_complete}'='true'::jsonb
 AND jsonb_typeof(t.spec#>'{request,payload,s_qc_policy}')='object'
 THEN NULL ELSE t.run_id::text END
 )::text,'UTF8')),'hex') AS series_id
 FROM ops_tasks t
 CROSS JOIN LATERAL (SELECT CASE WHEN jsonb_typeof(t.spec#>'{request,payload,requested_radars}')='array'
 THEN t.spec#>'{request,payload,requested_radars}' ELSE '[]'::jsonb END AS radars) requested
 WHERE t.kind='multiband' AND t.spec#>>'{request,payload,mode}'='sx_composite'
 AND t.state='SUCCEEDED' AND t.current_attempt IS NOT NULL
 AND t.result#>>'{asset,uri}' IS NOT NULL
 AND (t.spec#>>'{request,payload,analysis_time}')::timestamptz >= $1
 AND (t.spec#>>'{request,payload,analysis_time}')::timestamptz < $2
 AND NOT EXISTS(SELECT 1 FROM ops_retired_runs rr WHERE rr.run_id=t.run_id)
), series AS (
 SELECT DISTINCT ON (series_id) series_id,product_id,network_release,fingerprint,requested_radars,legacy,updated_at,id
 FROM eligible ORDER BY series_id,updated_at DESC,id DESC
), selected AS (
 SELECT coalesce(nullif($3::text,''),(SELECT series_id FROM series ORDER BY updated_at DESC,id DESC LIMIT 1),'') AS series_id
), frames AS (
 SELECT DISTINCT ON (at) result_id,analysis_time,at,eligible.series_id
 FROM eligible JOIN selected USING(series_id)
 ORDER BY at DESC,updated_at DESC,id DESC LIMIT 1500
)
SELECT jsonb_build_object(
 'selected_series_id',(SELECT series_id FROM selected),
 'series',coalesce((SELECT jsonb_agg(jsonb_build_object('series_id',series_id,'product_id',product_id,
 'network_release',network_release,'fingerprint',fingerprint,'requested_radars',requested_radars,'legacy',legacy) ORDER BY updated_at DESC,id DESC) FROM series),'[]'::jsonb),
 'items',coalesce((SELECT jsonb_agg(jsonb_build_object('result_id',result_id,'analysis_time',analysis_time,'series_id',series_id) ORDER BY at DESC) FROM frames),'[]'::jsonb))`

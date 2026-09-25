package operations

import (
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"math"
	"net/http"
	"strings"
	"time"
)

const radarWorkspacePrefix = "/api/v1/workspace/"
const xStationJoin = ` JOIN radars d ON d.radar_id=s.radar_id JOIN radar_config_versions c ON c.radar_id=d.radar_id AND c.radar_config_version=d.current_config_version `
const xBandPredicate = ` c.config#>>'{hardware,radar_band}'='X' `

// Candidate status never replaces the immutable ingest/automatic processing status.
const radarCandidateJoin = ` LEFT JOIN LATERAL (
 SELECT COALESCE(jsonb_agg(v.item ORDER BY v.finished_at DESC,v.id DESC),'[]'::jsonb) results FROM (
 SELECT t.id,t.updated_at finished_at,jsonb_build_object(
 'result_id',t.id::text||'.'||t.current_attempt::text,
 'finished_at',t.updated_at,'version',t.spec#>>'{identity,versions,network_release}') item
 FROM ops_tasks t WHERE t.kind='multiband' AND t.spec#>>'{request,payload,mode}'='x_qc'
 AND t.spec#>>'{request,payload,scan_id}'=s.scan_id::text
 AND lower(t.spec#>>'{request,payload,radar_id}')=lower(s.radar_id)
 AND t.state='SUCCEEDED' AND t.current_attempt IS NOT NULL AND t.result#>>'{asset,uri}' IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM ops_retired_runs rr WHERE rr.run_id=t.run_id)
 ORDER BY t.updated_at DESC,t.id DESC LIMIT 20) v
 ) q ON true LEFT JOIN LATERAL (
 SELECT state,error_message FROM ops_tasks t WHERE t.kind='multiband'
 AND t.spec#>>'{request,payload,mode}'='x_qc' AND t.spec#>>'{request,payload,scan_id}'=s.scan_id::text
 ORDER BY t.created_at DESC,t.id DESC LIMIT 1) latest ON true `

func (h *Handler) radarWorkspace(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method != http.MethodGet {
		writeProblem(w, problem(405, "method_not_allowed", "此目录只提供读取"))
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()
	r = r.WithContext(ctx)
	var value any
	var err error
	path := strings.TrimPrefix(r.URL.Path, radarWorkspacePrefix)
	switch path {
	case "radar-stations":
		value, err = h.radarStations(r)
	case "radar-scans":
		value, err = h.radarScans(r)
	default:
		value, err = h.radarProduct(w, r, strings.Split(path, "/"))
	}
	if err == errResponseWritten {
		return
	}
	if err != nil {
		writeProblem(w, err)
		return
	}
	_ = json.NewEncoder(w).Encode(value)
}

func (h *Handler) radarStations(r *http.Request) (any, error) {
	q := r.URL.Query()
	if q.Get("band") != "" && q.Get("band") != "X" {
		return nil, Invalid("当前独立目录支持 X 波段")
	}
	var start, end time.Time
	var err error
	if q.Get("start") != "" || q.Get("end") != "" {
		start, end, err = parseWindow(q, 24*time.Hour)
		if err != nil {
			return nil, err
		}
	}
	var earliest, latest sql.NullTime
	err = h.service.Store.DB.QueryRowContext(r.Context(), `SELECT min(s.volume_end_time),max(s.volume_end_time) FROM radar_scans s `+xStationJoin+` WHERE `+xBandPredicate).Scan(&earliest, &latest)
	if err != nil {
		return nil, err
	}
	if start.IsZero() {
		at := time.Now().UTC()
		if latest.Valid {
			at = latest.Time
		}
		loc := time.FixedZone("UTC+8", 8*3600)
		day := at.In(loc)
		start = time.Date(day.Year(), day.Month(), day.Day(), 0, 0, 0, 0, loc).UTC()
		end = start.Add(24 * time.Hour)
	}
	filter := Digest([]byte(start.String() + end.String() + "X"))
	after := ""
	if cursor := q.Get("cursor"); cursor != "" {
		b, e := base64.RawURLEncoding.DecodeString(cursor)
		if e != nil || len(b) > 256 {
			return nil, Invalid("站点游标非法")
		}
		p := strings.Split(string(b), ":")
		if len(p) != 2 || p[0] != filter || !namePattern.MatchString(p[1]) {
			return nil, Invalid("站点游标与筛选条件不符")
		}
		after = p[1]
	}
	rows, err := h.service.Store.DB.QueryContext(r.Context(), `WITH volumes AS (
 SELECT s.radar_id,s.scan_id,s.volume_end_time,r.normalized_uri,
 EXISTS(SELECT 1 FROM ops_tasks t WHERE t.kind='multiband' AND t.state='SUCCEEDED'
 AND t.spec#>>'{request,payload,mode}'='x_qc' AND t.spec#>>'{request,payload,scan_id}'=s.scan_id::text
 AND lower(t.spec#>>'{request,payload,radar_id}')=lower(s.radar_id)
 AND t.current_attempt IS NOT NULL AND t.result#>>'{asset,uri}' IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM ops_retired_runs rr WHERE rr.run_id=t.run_id)) ready
 FROM radar_scans s `+currentScanJoin+` WHERE s.volume_end_time >= $1 AND s.volume_end_time < $2
 ) SELECT jsonb_build_object('radar_id',d.radar_id,'display_name',COALESCE(d.display_name,d.radar_id),
 'band','X','geometry_status','unverified','registered',count(v.scan_id),
 'candidate_site',CASE WHEN jsonb_typeof(c.config#>'{site,longitude_deg}')='number'
 AND jsonb_typeof(c.config#>'{site,latitude_deg}')='number' THEN CASE WHEN
 (c.config#>>'{site,longitude_deg}')::numeric BETWEEN -180 AND 180 AND
 (c.config#>>'{site,latitude_deg}')::numeric BETWEEN -90 AND 90 THEN jsonb_build_object(
 'longitude_deg',c.config#>'{site,longitude_deg}',
 'latitude_deg',c.config#>'{site,latitude_deg}',
 'coordinate_source','draft_radar_config','config_version',d.current_config_version) ELSE NULL END ELSE NULL END,
 'normalized',count(NULLIF(v.normalized_uri,'')),'qc_ready',count(*) FILTER(WHERE v.ready),
 'latest_observation',max(v.volume_end_time))
 FROM radars d JOIN radar_config_versions c ON c.radar_id=d.radar_id AND c.radar_config_version=d.current_config_version
 LEFT JOIN volumes v ON v.radar_id=d.radar_id WHERE `+xBandPredicate+` AND d.radar_id>$3
 GROUP BY d.radar_id,d.display_name,d.current_config_version,c.config ORDER BY d.radar_id LIMIT 101`, start, end, after)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items, err := rawRows(rows)
	if err != nil {
		return nil, err
	}
	next := ""
	if len(items) > 100 {
		items = items[:100]
		var last struct {
			ID string `json:"radar_id"`
		}
		_ = json.Unmarshal(items[99], &last)
		next = base64.RawURLEncoding.EncodeToString([]byte(filter + ":" + last.ID))
	}
	var a, b any
	if earliest.Valid {
		a = earliest.Time
	}
	if latest.Valid {
		b = latest.Time
	}
	return map[string]any{"items": items, "start": start, "end": end, "available_range": map[string]any{"start": a, "end": b}, "next_cursor": next, "inventory_scope": "registered_catalog"}, nil
}
func (h *Handler) radarScans(r *http.Request) (any, error) {
	q := r.URL.Query()
	start, end, err := parseWindow(q, 24*time.Hour)
	if err != nil {
		return nil, err
	}
	radar := strings.ToLower(q.Get("radar_id"))
	if !namePattern.MatchString(radar) {
		return nil, Invalid("雷达站号非法")
	}
	filter := Digest([]byte(start.String() + end.String() + radar))
	before, id, err := decodeCursor(q.Get("cursor"), filter)
	if err != nil {
		return nil, err
	}
	rows, err := h.service.Store.DB.QueryContext(r.Context(), `SELECT jsonb_build_object(
 'scan_id',s.scan_id,'radar_id',s.radar_id,'volume_start',s.volume_start_time,'volume_end',s.volume_end_time,
 'state',COALESCE(r.status,'REGISTERED'),'qc_status',CASE WHEN jsonb_array_length(q.results)>0 THEN 'READY'
 WHEN latest.state='SUCCEEDED' THEN 'ASSET_UNAVAILABLE' ELSE COALESCE(latest.state,CASE WHEN NULLIF(r.normalized_uri,'') IS NULL THEN 'WAITING_DECODE' ELSE 'WAITING_QC' END) END,
 'error_message',COALESCE(latest.error_message,''),'results',q.results)
 FROM radar_scans s `+xStationJoin+currentScanJoin+radarCandidateJoin+` WHERE `+xBandPredicate+`
 AND lower(s.radar_id)=$3 AND s.volume_end_time >= $1 AND s.volume_end_time < $2
 AND ($4::timestamptz IS NULL OR (s.volume_end_time,s.scan_id)<($4,$5::uuid))
 ORDER BY s.volume_end_time DESC,s.scan_id DESC LIMIT 101`, start, end, radar, nullableTime(before), nullableUUID(id))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items, err := rawRows(rows)
	if err != nil {
		return nil, err
	}
	next := ""
	if len(items) > 100 {
		items = items[:100]
		var last struct {
			ID string    `json:"scan_id"`
			At time.Time `json:"volume_end"`
		}
		_ = json.Unmarshal(items[99], &last)
		next = encodeCursor(last.At, last.ID, filter)
	}
	return map[string]any{"items": items, "next_cursor": next}, nil
}

type radarSweep struct {
	Number    int     `json:"sweep_number"`
	Sequence  int     `json:"sequence"`
	Elevation float64 `json:"elevation_deg"`
	Raw       string  `json:"raw"`
	QC        string  `json:"qc"`
	Flags     string  `json:"flags"`
}
type radarManifest struct {
	Radar       string          `json:"radar_id"`
	Scan        string          `json:"scan_id"`
	Start       time.Time       `json:"volume_start"`
	End         time.Time       `json:"volume_end"`
	Candidate   bool            `json:"candidate_only"`
	Operational bool            `json:"operational_eligible"`
	Geometry    json.RawMessage `json:"geometry"`
	Legend      json.RawMessage `json:"legend"`
	Comparison  struct {
		Sweeps []radarSweep `json:"sweeps"`
	} `json:"comparison"`
}

func parseRadarManifest(raw []byte, radar, scan string) (radarManifest, error) {
	var m radarManifest
	if json.Unmarshal(raw, &m) != nil || m.Radar != radar || m.Scan != scan || m.Start.IsZero() || m.End.Before(m.Start) || !m.Candidate || m.Operational || len(m.Comparison.Sweeps) == 0 || len(m.Comparison.Sweeps) > 64 {
		return m, Conflict("质控图件身份或扫层清单不匹配")
	}
	seen := map[int]bool{}
	for _, s := range m.Comparison.Sweeps {
		if seen[s.Number] || s.Number < 0 || s.Sequence < 1 || s.Raw == s.QC || math.IsNaN(s.Elevation) || math.IsInf(s.Elevation, 0) {
			return m, Conflict("扫层编号或仰角非法")
		}
		seen[s.Number] = true
		for _, k := range []string{s.Raw, s.QC, s.Flags} {
			if !safeKey(k) || !strings.HasSuffix(k, ".png") {
				return m, Conflict("图件路径非法")
			}
		}
	}
	return m, nil
}
func (h *Handler) radarProduct(w http.ResponseWriter, r *http.Request, parts []string) (any, error) {
	if len(parts) != 2 && len(parts) != 4 || parts[0] != "radar-products" {
		return nil, ErrNotFound
	}
	if len(parts) == 4 && parts[2] != "assets" {
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
	var request struct {
		Payload struct {
			Mode  string `json:"mode"`
			Radar string `json:"radar_id"`
			Scan  string `json:"scan_id"`
		} `json:"payload"`
	}
	if json.Unmarshal(t.Spec.Request, &request) != nil || request.Payload.Mode != "x_qc" || t.Spec.Kind != "multiband" {
		return nil, ErrNotFound
	}
	if t.State != "SUCCEEDED" || t.CurrentAttempt != ids[1] {
		return nil, problem(410, "result_unavailable", "所选结果版本已不可用，请重新选择")
	}
	raw, _, err := h.service.Asset(r.Context(), t.ID, "manifest.json")
	if err != nil {
		return nil, err
	}
	m, err := parseRadarManifest(raw, request.Payload.Radar, request.Payload.Scan)
	if err != nil {
		return nil, err
	}
	if len(parts) == 4 {
		b, e := base64.RawURLEncoding.DecodeString(parts[3])
		if e != nil || len(b) > 512 {
			return nil, Invalid("图件身份非法")
		}
		key := string(b)
		allowed := false
		for _, s := range m.Comparison.Sweeps {
			if key == s.Raw || key == s.QC || key == s.Flags {
				allowed = true
			}
		}
		if !allowed {
			return nil, ErrNotFound
		}
		data, media, e := h.service.Asset(r.Context(), t.ID, key)
		if e != nil {
			return nil, e
		}
		w.Header().Set("Content-Type", media)
		_, _ = w.Write(data)
		return nil, errResponseWritten
	}
	for i := range m.Comparison.Sweeps {
		s := &m.Comparison.Sweeps[i]
		for _, key := range []*string{&s.Raw, &s.QC, &s.Flags} {
			*key = radarWorkspacePrefix + "radar-products/" + parts[1] + "/assets/" + base64.RawURLEncoding.EncodeToString([]byte(*key))
		}
	}
	return map[string]any{"result_id": parts[1], "radar_id": m.Radar, "scan_id": m.Scan, "volume_start": m.Start, "volume_end": m.End, "candidate_only": true, "operational_eligible": false, "geometry": m.Geometry, "legend": m.Legend, "sweeps": m.Comparison.Sweeps}, nil
}

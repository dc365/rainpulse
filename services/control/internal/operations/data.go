package operations

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"time"
)

// DataQuery describes a bounded catalog window, not a meteorological cadence.
// A registered volume and an object verified in storage are different facts.
type DataQuery struct {
	Start time.Time `json:"start"`
	End   time.Time `json:"end"`
	Radar string    `json:"radar"`
	Stage string    `json:"stage"`
}

func ParseDataQuery(q url.Values) (DataQuery, error) {
	start, end, err := parseWindow(q, 24*time.Hour)
	if err != nil {
		return DataQuery{}, err
	}
	d := DataQuery{start, end, strings.ToLower(strings.TrimSpace(q.Get("radar"))), q.Get("stage")}
	if d.Radar != "" && !namePattern.MatchString(d.Radar) {
		return d, Invalid("雷达站号非法")
	}
	switch d.Stage {
	case "", "all", "normalized_missing", "qc_missing", "grid_missing", "failed":
	default:
		return d, Invalid("数据阶段过滤非法")
	}
	if d.Stage == "" {
		d.Stage = "all"
	}
	return d, nil
}
func parseWindow(q url.Values, maximum time.Duration) (time.Time, time.Time, error) {
	start, e := time.Parse(time.RFC3339Nano, q.Get("start"))
	if e != nil {
		return time.Time{}, time.Time{}, Invalid("开始时间须为带时区的时间")
	}
	end, e := time.Parse(time.RFC3339Nano, q.Get("end"))
	if e != nil || !end.After(start) || end.Sub(start) > maximum {
		return time.Time{}, time.Time{}, Invalid("时间范围为空或超出查询上限")
	}
	return start.UTC(), end.UTC(), nil
}
func (q DataQuery) filter() string { return CanonicalDigest(q) }

const currentScanJoin = ` LEFT JOIN LATERAL (
 SELECT run_id,status,normalized_uri,qc_uri,grid_uri,radar_config_version,created_at,updated_at
 FROM radar_scan_runs WHERE scan_id=s.scan_id ORDER BY created_at DESC,run_id DESC LIMIT 1
) r ON true `
const dataWindow = ` s.volume_end_time >= $1 AND s.volume_end_time < $2
 AND ($3='' OR lower(s.radar_id)=$3) `
const dataStage = ` AND ($4='all'
 OR ($4='normalized_missing' AND NULLIF(r.normalized_uri,'') IS NULL)
 OR ($4='qc_missing' AND NULLIF(r.qc_uri,'') IS NULL)
 OR ($4='grid_missing' AND NULLIF(r.grid_uri,'') IS NULL)
 OR ($4='failed' AND r.status='FAILED')) `
const dataObject = `jsonb_build_object('id',s.scan_id,'radar_id',s.radar_id,
 'observed_at',s.volume_end_time,'received_at',s.received_at,'run_id',r.run_id,
 'state',COALESCE(r.status,'REGISTERED'),'normalized_uri',r.normalized_uri,
 'qc_uri',r.qc_uri,'grid_uri',r.grid_uri,'config_version',r.radar_config_version,
 'verification','catalog_only','updated_at',r.updated_at)`

func (s *Store) DataScans(ctx context.Context, q DataQuery, limit int, before time.Time, id string) ([]json.RawMessage, error) {
	rows, e := s.DB.QueryContext(ctx, `SELECT `+dataObject+` FROM radar_scans s `+currentScanJoin+` WHERE `+dataWindow+dataStage+`
 AND ($5::timestamptz IS NULL OR (s.volume_end_time,s.scan_id)<($5,$6::uuid))
 ORDER BY s.volume_end_time DESC,s.scan_id DESC LIMIT $7`, q.Start, q.End, q.Radar, q.Stage, nullableTime(before), nullableUUID(id), limit+1)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	return rawRows(rows)
}
func rawRows(rows *sql.Rows) ([]json.RawMessage, error) {
	out := []json.RawMessage{}
	for rows.Next() {
		var b []byte
		if e := rows.Scan(&b); e != nil {
			return nil, e
		}
		out = append(out, json.RawMessage(b))
	}
	return out, rows.Err()
}
func (s *Store) DataSummary(ctx context.Context, q DataQuery) ([]json.RawMessage, error) {
	// Include known stations even when no scan arrived in the selected window.
	rows, e := s.DB.QueryContext(ctx, `WITH volumes AS (
 SELECT s.radar_id,s.volume_end_time,r.status,r.normalized_uri,r.qc_uri,r.grid_uri
 FROM radar_scans s `+currentScanJoin+` WHERE `+dataWindow+`
 ), stations AS (
 SELECT lower(radar_id) radar_id FROM radars WHERE ($3='' OR lower(radar_id)=$3)
 UNION SELECT lower(radar_id) FROM volumes
 ) SELECT jsonb_build_object('radar_id',a.radar_id,'registered',count(v.radar_id),
 'normalized',count(NULLIF(v.normalized_uri,'')),'qc',count(NULLIF(v.qc_uri,'')),
 'grid',count(NULLIF(v.grid_uri,'')),'failed',count(*) FILTER(WHERE v.status='FAILED'),
 'latest_observation',max(v.volume_end_time),'verification','catalog_only')
 FROM stations a LEFT JOIN volumes v ON lower(v.radar_id)=a.radar_id
 GROUP BY a.radar_id ORDER BY a.radar_id`, q.Start, q.End, q.Radar)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	return rawRows(rows)
}
func (s *Store) DataScan(ctx context.Context, id string) (json.RawMessage, error) {
	var raw []byte
	e := s.DB.QueryRowContext(ctx, `SELECT `+dataObject+` FROM radar_scans s `+currentScanJoin+` WHERE s.scan_id=$1`, id).Scan(&raw)
	if errors.Is(e, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return raw, e
}

type ScanLineage struct {
	Scan       json.RawMessage   `json:"scan"`
	Automatic  []json.RawMessage `json:"automatic_tasks"`
	Analyses   []json.RawMessage `json:"downstream_analyses"`
	Candidates []json.RawMessage `json:"candidate_tasks"`
	Checks     []json.RawMessage `json:"checks"`
	Truncated  map[string]bool   `json:"truncated"`
	SampledAt  time.Time         `json:"sampled_at"`
}

func (s *Store) ScanLineage(ctx context.Context, id string) (ScanLineage, error) {
	d := ScanLineage{Truncated: map[string]bool{}, SampledAt: time.Now().UTC()}
	var e error
	d.Scan, e = s.DataScan(ctx, id)
	if e != nil {
		return d, e
	}
	queries := []struct {
		name, sql string
		out       *[]json.RawMessage
	}{
		{"automatic_tasks", `SELECT jsonb_build_object('id',j.job_id,'run_id',j.run_id,'kind',j.job_type,'state',j.status,'config_version',j.config_version,'created_at',j.created_at)
 FROM jobs j WHERE j.run_id IN(SELECT run_id FROM radar_scan_runs WHERE scan_id=$1)
 ORDER BY j.created_at DESC,j.job_id DESC LIMIT 101`, &d.Automatic},
		{"downstream_analyses", `SELECT jsonb_build_object('id',a.analysis_id,'analysis_time',a.analysis_time,'grid_id',a.grid_id,'state',a.status,'contribution_state',c.state,'mosaic_uri',a.mosaic_uri,'analysis_uri',a.analysis_uri,'config_version',a.config_version)
 FROM analysis_cycle_radars c JOIN analysis_cycles a ON a.analysis_id=c.analysis_id
 WHERE c.scan_id=$1 ORDER BY a.analysis_time DESC,a.analysis_id DESC LIMIT 101`, &d.Analyses},
		{"candidate_tasks", `SELECT jsonb_build_object('id',t.id,'run_id',t.run_id,'kind',t.kind,'state',t.state,'name',t.spec->>'name','fingerprint',t.spec#>>'{identity,fingerprint}','created_at',t.created_at)
 FROM ops_tasks t WHERE t.spec#>>'{request,payload,scan_id}'=$1::text ORDER BY t.created_at DESC,t.id DESC LIMIT 101`, &d.Candidates},
		{"checks", `SELECT result FROM ops_asset_checks WHERE scan_id=$1 ORDER BY stage`, &d.Checks},
	}
	for _, v := range queries {
		rows, e := s.DB.QueryContext(ctx, v.sql, id)
		if e != nil {
			return d, e
		}
		items, e := rawRows(rows)
		rows.Close()
		if e != nil {
			return d, e
		}
		if len(items) > 100 {
			d.Truncated[v.name] = true
			items = items[:100]
		}
		*v.out = items
	}
	return d, nil
}

type AssetCheck struct {
	ScanID    string    `json:"scan_id"`
	Stage     string    `json:"stage"`
	URI       string    `json:"uri"`
	State     string    `json:"state"`
	CheckedAt time.Time `json:"checked_at"`
	Scope     string    `json:"scope"`
	Message   string    `json:"message"`
	Asset     *AssetRef `json:"asset,omitempty"`
}

func scanAssetURI(raw json.RawMessage, stage string) (string, error) {
	key := ""
	switch stage {
	case "normalized":
		key = "normalized_uri"
	case "qc":
		key = "qc_uri"
	case "grid":
		key = "grid_uri"
	default:
		return "", Invalid("仅支持标准化、QC或格点资产检查")
	}
	var v map[string]json.RawMessage
	if e := json.Unmarshal(raw, &v); e != nil {
		return "", e
	}
	uri := jsonString(v[key])
	if uri == "" {
		return "", Conflict("该阶段没有资产登记，不会猜测存储路径")
	}
	return uri, nil
}
func (s *Service) CheckScanAsset(ctx context.Context, id, stage string) (AssetCheck, error) {
	raw, e := s.Store.DataScan(ctx, id)
	if e != nil {
		return AssetCheck{}, e
	}
	uri, e := scanAssetURI(raw, stage)
	if e != nil {
		return AssetCheck{}, e
	}
	checked := AssetCheck{ScanID: id, Stage: stage, URI: uri, State: "unverified", Scope: "completion_marker_only", Message: "完成标记不可用或不合法；不能据此判定整个资产缺失", CheckedAt: s.now()}
	ref, e := s.Probe(ctx, uri)
	if e == nil {
		checked.State = "marker_checked"
		checked.Asset = &ref
		checked.Message = "完成标记结构与声明摘要已核对；未重新下载校验全部数据对象，也不是气象质量验收"
	}
	if ctx.Err() != nil {
		return AssetCheck{}, ctx.Err()
	}
	// Re-read the pointer: don't present a check of an old URI as current evidence.
	current, e := s.Store.DataScan(ctx, id)
	if e != nil {
		return checked, e
	}
	currentURI, e := scanAssetURI(current, stage)
	if e != nil || currentURI != uri {
		return checked, Conflict("检查期间资产引用已变化，请刷新后重试")
	}
	_, e = s.Store.DB.ExecContext(ctx, `INSERT INTO ops_asset_checks(scan_id,stage,uri,state,checked_at,result) VALUES($1,$2,$3,$4,$5,$6)
 ON CONFLICT(scan_id,stage) DO UPDATE SET uri=EXCLUDED.uri,state=EXCLUDED.state,checked_at=EXCLUDED.checked_at,result=EXCLUDED.result
 WHERE ops_asset_checks.checked_at<=EXCLUDED.checked_at`, id, stage, uri, checked.State, checked.CheckedAt, string(JSON(checked)))
	return checked, e
}

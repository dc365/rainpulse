package operations

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"
)

func (h *Handler) extensions(r *http.Request, path string) (any, error) {
	ctx, q, s := r.Context(), r.URL.Query(), h.service
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if r.Method == http.MethodGet && (path == "/data/scans" || path == "/data/summary") {
		filter, e := ParseDataQuery(q)
		if e != nil {
			return nil, e
		}
		if path == "/data/summary" {
			items, e := s.Store.DataSummary(ctx, filter)
			return map[string]any{"items": items, "sampled_at": s.now(), "scope": "registered_volumes_catalog", "cadence_evaluated": false}, e
		}
		limit, e := integer(q.Get("limit"), 50, 100)
		if e != nil {
			return nil, e
		}
		before, id, e := decodeCursor(q.Get("cursor"), filter.filter())
		if e != nil {
			return nil, e
		}
		items, e := s.Store.DataScans(ctx, filter, limit, before, id)
		if e != nil {
			return nil, e
		}
		next := ""
		if len(items) > limit {
			items = items[:limit]
			var last struct {
				ID         string    `json:"id"`
				ObservedAt time.Time `json:"observed_at"`
			}
			if e = json.Unmarshal(items[len(items)-1], &last); e != nil {
				return nil, e
			}
			next = encodeCursor(last.ObservedAt, last.ID, filter.filter())
		}
		return map[string]any{"items": items, "next_cursor": next, "sampled_at": s.now(), "verification": "catalog_only"}, nil
	}
	if len(parts) >= 3 && parts[0] == "data" && parts[1] == "scans" {
		id, e := pathID(parts, 2)
		if e != nil {
			return nil, e
		}
		if len(parts) == 3 && r.Method == http.MethodGet {
			return s.Store.ScanLineage(ctx, id)
		}
		if len(parts) == 4 && parts[3] == "probe" && r.Method == http.MethodPost {
			var body struct {
				Stage string `json:"stage"`
			}
			if e = decodeBody(r, &body); e != nil {
				return nil, e
			}
			return s.CheckScanAsset(ctx, id, body.Stage)
		}
	}
	if path == "/performance" && r.Method == http.MethodGet {
		filter, e := ParsePerformanceQuery(q)
		if e != nil {
			return nil, e
		}
		return s.Store.Performance(ctx, filter)
	}
	if path == "/pools" && r.Method == http.MethodGet {
		items, e := s.Store.Pools(ctx)
		return map[string]any{"items": items, "sampled_at": s.now(), "scope": "management_only"}, e
	}
	if len(parts) == 3 && parts[0] == "pools" && parts[2] == "action" && r.Method == http.MethodPost {
		var a PoolAction
		if e := decodeBody(r, &a); e != nil {
			return nil, e
		}
		if e := s.Store.PoolAction(ctx, parts[1], a, "administrator"); e != nil {
			return nil, e
		}
		items, e := s.Store.Pools(ctx)
		return map[string]any{"items": items, "sampled_at": s.now()}, e
	}
	if path == "/pools/events" && r.Method == http.MethodGet {
		before := int64(0)
		var e error
		if q.Get("before") != "" {
			before, e = strconv.ParseInt(q.Get("before"), 10, 64)
			if e != nil || before < 0 {
				return nil, Invalid("操作记录游标非法")
			}
		}
		rows, e := s.Store.DB.QueryContext(ctx, `SELECT jsonb_build_object('id',id,'kind',kind,'action',action,'revision',revision,'actor',actor,'reason',reason,'at',at)
 FROM ops_resource_events WHERE ($1::bigint=0 OR id<$1) ORDER BY id DESC LIMIT 51`, before)
		if e != nil {
			return nil, e
		}
		defer rows.Close()
		items, e := rawRows(rows)
		if e != nil {
			return nil, e
		}
		next := int64(0)
		if len(items) > 50 {
			items = items[:50]
			var last struct {
				ID int64 `json:"id"`
			}
			if e = json.Unmarshal(items[49], &last); e != nil {
				return nil, e
			}
			next = last.ID
		}
		return map[string]any{"items": items, "next_before": next, "sampled_at": s.now()}, nil
	}
	return nil, problem(404, "not_found", "管理接口不存在")
}

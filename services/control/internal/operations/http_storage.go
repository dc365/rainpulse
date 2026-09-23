package operations

import (
	"net/http"
	"strings"
)

func (h *Handler) storage(r *http.Request, path string) (any, error) {
	s, ctx := h.service.Store, r.Context()
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if r.Method == "GET" && path == "/releases" {
		return s.ReleaseCatalog(ctx)
	}
	if r.Method == "POST" && len(parts) == 3 && parts[0] == "releases" && parts[2] == "select" {
		var a ReleaseChoice
		if e := decodeBody(r, &a); e != nil {
			return nil, e
		}
		return nil, s.ChooseRelease(ctx, parts[1], a)
	}
	if r.Method == "GET" && path == "/storage" {
		return s.StorageStatus(ctx)
	}
	if r.Method == "POST" && path == "/storage/report" {
		var a StorageReport
		if e := decodeBody(r, &a); e != nil {
			return nil, e
		}
		return nil, s.ReportStorage(ctx, a)
	}
	if r.Method == "POST" && path == "/storage/pressure" {
		var a PressureAction
		if e := decodeBody(r, &a); e != nil {
			return nil, e
		}
		return nil, s.SetPressure(ctx, a)
	}
	if len(parts) == 4 && parts[0] == "storage" && parts[1] == "runs" && parts[3] == "pin" && r.Method == "POST" {
		id, e := pathID(parts, 2)
		if e != nil {
			return nil, e
		}
		var a struct {
			Pin    bool   `json:"pin"`
			Reason string `json:"reason"`
		}
		if e = decodeBody(r, &a); e != nil {
			return nil, e
		}
		return nil, s.PinStorage(ctx, id, a.Reason, a.Pin)
	}
	if r.Method == "GET" && path == "/storage/pins" {
		rows, e := s.DB.QueryContext(ctx, `SELECT jsonb_build_object('run_id',p.run_id,'name',r.name,'reason',p.reason,'at',p.updated_at) FROM ops_retention_pins p JOIN ops_runs r ON r.id=p.run_id ORDER BY p.updated_at DESC LIMIT 200`)
		if e != nil {
			return nil, e
		}
		defer rows.Close()
		items, e := rawRows(rows)
		return map[string]any{"items": items}, e
	}
	if r.Method == "POST" && path == "/storage/cleanup/plans" {
		var p RetentionPolicy
		if e := decodeBody(r, &p); e != nil {
			return nil, e
		}
		return s.PreviewRetention(ctx, p)
	}
	if len(parts) >= 4 && parts[0] == "storage" && parts[1] == "cleanup" && parts[2] == "plans" {
		id, e := pathID(parts, 3)
		if e != nil {
			return nil, e
		}
		if len(parts) == 4 && r.Method == "GET" {
			p, state, e := s.RetentionPlan(ctx, id)
			return map[string]any{"plan": p, "state": state}, e
		}
		if len(parts) == 5 && r.Method == "POST" && parts[4] == "begin" {
			var a struct {
				Digest string `json:"digest"`
			}
			if e := decodeBody(r, &a); e != nil {
				return nil, e
			}
			return s.BeginRetention(ctx, id, a.Digest)
		}
		if len(parts) == 5 && r.Method == "POST" && parts[4] == "finish" {
			var a PurgeReceipt
			if e := decodeBody(r, &a); e != nil {
				return nil, e
			}
			return nil, s.FinishRetention(ctx, id, a)
		}
	}
	return nil, problem(404, "not_found", "存储管理接口不存在")
}

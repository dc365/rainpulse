package operations

import (
	"context"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"
)

type HTTPOptions struct {
	AdminToken    string
	AdminAuthMode string
	WorkerToken   string
	Admit         func(context.Context) (func(), error)
	LokiURL       string
}

const AdminAuthModeValidation = "validation"

type Handler struct {
	service *Service
	options HTTPOptions
	next    http.Handler
}

func NewHandler(service *Service, next http.Handler, options HTTPOptions) http.Handler {
	return &Handler{service: service, next: next, options: options}
}
func authorized(value, token string) bool {
	return token != "" && subtle.ConstantTimeCompare([]byte(value), []byte("Bearer "+token)) == 1
}
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	const admin = "/api/v1/admin/ops"
	const internal = "/internal/ops/v1"
	worker := strings.HasPrefix(r.URL.Path, internal+"/")
	isAdmin := r.URL.Path == admin || strings.HasPrefix(r.URL.Path, admin+"/")
	if !worker && !isAdmin {
		h.next.ServeHTTP(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if isAdmin && r.Method == http.MethodGet && r.URL.Path == admin+"/auth-mode" {
		mode := "credential"
		if h.options.AdminAuthMode == AdminAuthModeValidation {
			mode = AdminAuthModeValidation
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"mode": mode})
		return
	}
	token := h.options.AdminToken
	if worker {
		token = h.options.WorkerToken
	}
	if !(isAdmin && h.options.AdminAuthMode == AdminAuthModeValidation) && !authorized(r.Header.Get("Authorization"), token) {
		writeProblem(w, problem(401, "unauthorized", "请使用已配置的管理凭据"))
		return
	}
	timeout := 15 * time.Second
	if strings.Contains(r.URL.Path, "/plans") || strings.HasSuffix(r.URL.Path, "/action") || strings.Contains(r.URL.Path, "/storage/cleanup/") {
		timeout = 2 * time.Minute
	}
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	r = r.WithContext(ctx)
	path := strings.TrimPrefix(r.URL.Path, admin)
	if worker {
		path = strings.TrimPrefix(r.URL.Path, internal)
	}
	if r.Method != http.MethodGet && (!worker || path == "/claim") && h.options.Admit != nil {
		release, err := h.options.Admit(ctx)
		if err != nil {
			writeProblem(w, problem(503, "release_paused", "发布维护期间暂停新操作；日志、心跳和结果登记仍可用"))
			return
		}
		defer release()
	}
	var value any
	var err error
	if worker {
		value, err = h.internal(r, path)
	} else {
		value, err = h.admin(w, r, path)
	}
	if errors.Is(err, errResponseWritten) {
		return
	}
	if err != nil {
		status, code, _ := errorStatus(err)
		slog.Warn("operations request rejected", "path", r.URL.Path, "status", status, "code", code, "error", Redact(err.Error()))
		writeProblem(w, err)
		return
	}
	if value == nil {
		value = map[string]any{"ok": true}
	}
	_ = json.NewEncoder(w).Encode(value)
}

var errResponseWritten = errors.New("response written")

func writeProblem(w http.ResponseWriter, err error) {
	status, code, message := errorStatus(err)
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"code": code, "message": message})
}
func decodeBody(r *http.Request, target any) error {
	// No unknown fields, trailing JSON or unbounded request bodies.
	data, err := io.ReadAll(io.LimitReader(r.Body, 1024*1024+1))
	if err != nil {
		return Invalid("读取请求失败")
	}
	if len(data) > 1024*1024 {
		return problem(413, "body_too_large", "请求超过1 MiB")
	}
	d := json.NewDecoder(strings.NewReader(string(data)))
	d.DisallowUnknownFields()
	if err = d.Decode(target); err != nil {
		return Invalid("请求JSON字段或格式不正确")
	}
	if err = d.Decode(new(any)); err != io.EOF {
		return Invalid("请求只能包含一个JSON对象")
	}
	return nil
}
func integer(raw string, def, max int) (int, error) {
	if raw == "" {
		return def, nil
	}
	v, err := strconv.Atoi(raw)
	if err != nil || v < 1 || v > max {
		return 0, Invalid("分页大小非法")
	}
	return v, nil
}

type pageCursor struct {
	At     time.Time `json:"at"`
	ID     string    `json:"id"`
	Filter string    `json:"filter"`
}

func decodeCursor(raw, filter string) (time.Time, string, error) {
	if raw == "" {
		return time.Time{}, "", nil
	}
	if len(raw) > 1024 {
		return time.Time{}, "", Invalid("分页游标过长")
	}
	b, err := base64.RawURLEncoding.DecodeString(raw)
	if err != nil {
		return time.Time{}, "", Invalid("分页游标非法")
	}
	var c pageCursor
	if json.Unmarshal(b, &c) != nil || c.At.IsZero() || !ValidID(c.ID) || c.Filter != filter {
		return time.Time{}, "", Invalid("分页过滤条件发生变化")
	}
	return c.At, c.ID, nil
}
func encodeCursor(at time.Time, id, filter string) string {
	return base64.RawURLEncoding.EncodeToString(JSON(pageCursor{at, id, filter}))
}
func pathID(parts []string, index int) (string, error) {
	if len(parts) <= index || !ValidID(parts[index]) {
		return "", Invalid("路径ID非法")
	}
	return parts[index], nil
}
func (h *Handler) admin(w http.ResponseWriter, r *http.Request, path string) (any, error) {
	ctx := r.Context()
	s := h.service
	parts := strings.Split(strings.Trim(path, "/"), "/")
	q := r.URL.Query()
	if path == "/releases" || strings.HasPrefix(path, "/releases/") || path == "/storage" || strings.HasPrefix(path, "/storage/") {
		return h.storage(r, path)
	}
	if strings.HasPrefix(path, "/data/") || path == "/performance" || path == "/pools" || strings.HasPrefix(path, "/pools/") {
		return h.extensions(r, path)
	}
	if r.Method == "GET" && path == "/status" {
		ready := s.Store.Ready(ctx) == nil
		workers := []WorkerInfo{}
		counts := map[string]int{}
		if ready {
			var err error
			workers, err = s.Store.Workers(ctx)
			if err != nil {
				return nil, err
			}
			counts, err = s.Store.Counts(ctx)
			if err != nil {
				return nil, err
			}
		}
		logs := "not_configured"
		if h.options.LokiURL != "" {
			logs = "configured"
		}
		return map[string]any{"schema_version": Version, "database_ready": ready, "worker_auth_configured": h.options.WorkerToken != "", "system_logs": logs, "workers": workers, "counts": counts, "sampled_at": s.now(), "mode": "candidate_only", "management_schema": 3}, nil
	}
	if r.Method == "GET" && (path == "/legacy" || path == "/runs") {
		limit, err := integer(q.Get("limit"), 50, 100)
		if err != nil {
			return nil, err
		}
		search := q.Get("q")
		if len(search) > 128 {
			return nil, Invalid("搜索条件过长")
		}
		filter := Digest([]byte(path + q.Get("state") + "\x00" + search))
		before, id, err := decodeCursor(q.Get("cursor"), filter)
		if err != nil {
			return nil, err
		}
		if path == "/legacy" {
			items, err := s.Store.Legacy(ctx, search, q.Get("state"), limit, before, id)
			if err != nil {
				return nil, err
			}
			next := ""
			if len(items) > limit {
				items = items[:limit]
				var last struct {
					ID        string    `json:"id"`
					CreatedAt time.Time `json:"created_at"`
				}
				_ = json.Unmarshal(items[limit-1], &last)
				next = encodeCursor(last.CreatedAt, last.ID, filter)
			}
			return map[string]any{"items": items, "next_cursor": next, "sampled_at": s.now()}, nil
		}
		items, err := s.Store.Runs(ctx, q.Get("state"), search, before, id, limit)
		if err != nil {
			return nil, err
		}
		next := ""
		if len(items) > limit {
			items = items[:limit]
			last := items[limit-1]
			next = encodeCursor(last.CreatedAt, last.ID, filter)
		}
		return map[string]any{"items": items, "next_cursor": next, "sampled_at": s.now()}, nil
	}
	if path == "/workers" && r.Method == "GET" {
		v, e := s.Store.Workers(ctx)
		return map[string]any{"items": v, "sampled_at": s.now()}, e
	}
	if path == "/inventory" && r.Method == "GET" {
		start, e := time.Parse(time.RFC3339, q.Get("start"))
		if e != nil {
			return nil, Invalid("起始时间需包含时区")
		}
		end, e := time.Parse(time.RFC3339, q.Get("end"))
		if e != nil || !end.After(start) || end.Sub(start) > 24*time.Hour {
			return nil, Invalid("请选择不超过24小时的时间范围")
		}
		limit, e := integer(q.Get("limit"), 100, 100)
		if e != nil {
			return nil, e
		}
		filter := Digest([]byte(start.String() + end.String() + q.Get("radar")))
		before, id, e := decodeCursor(q.Get("cursor"), filter)
		if e != nil {
			return nil, e
		}
		items, e := s.Store.Inventory(ctx, start, end, q.Get("radar"), limit, before, id)
		if e != nil {
			return nil, e
		}
		next := ""
		if len(items) > limit {
			items = items[:limit]
			var last struct {
				ID string    `json:"id"`
				At time.Time `json:"observed_at"`
			}
			_ = json.Unmarshal(items[limit-1], &last)
			next = encodeCursor(last.At, last.ID, filter)
		}
		return map[string]any{"items": items, "next_cursor": next, "verification": "catalog_only"}, nil
	}
	if path == "/plans" && r.Method == "POST" {
		var selection Selection
		if e := decodeBody(r, &selection); e != nil {
			return nil, e
		}
		return s.Preflight(ctx, selection)
	}
	if len(parts) >= 2 && parts[0] == "plans" {
		id, e := pathID(parts, 1)
		if e != nil {
			return nil, e
		}
		if r.Method == "GET" && len(parts) == 2 {
			return s.Store.Plan(ctx, id)
		}
		if r.Method == "POST" && len(parts) == 3 && parts[2] == "submit" {
			var req struct {
				Key string `json:"idempotency_key"`
			}
			if e = decodeBody(r, &req); e != nil {
				return nil, e
			}
			run, e := s.Submit(ctx, id, req.Key, "administrator")
			return map[string]any{"run_id": run, "accepted": e == nil}, e
		}
	}
	if len(parts) >= 2 && parts[0] == "runs" {
		id, e := pathID(parts, 1)
		if e != nil {
			return nil, e
		}
		if r.Method == "GET" && len(parts) == 2 {
			return s.Store.Run(ctx, id)
		}
		if len(parts) == 3 && parts[2] == "action" && r.Method == "POST" {
			var req struct {
				Action string `json:"action"`
			}
			if e = decodeBody(r, &req); e != nil {
				return nil, e
			}
			if e = s.Action(ctx, id, req.Action, "administrator"); e != nil {
				return nil, e
			}
			return s.Store.Run(ctx, id)
		}
		if len(parts) == 3 && parts[2] == "events" && r.Method == "GET" {
			return h.events(r, id, "")
		}
	}
	if len(parts) == 2 && parts[0] == "legacy" && r.Method == "GET" {
		id, e := pathID(parts, 1)
		if e != nil {
			return nil, e
		}
		return s.Store.LegacyDetail(ctx, id)
	}
	if len(parts) >= 2 && parts[0] == "tasks" {
		id, e := pathID(parts, 1)
		if e != nil {
			return nil, e
		}
		if len(parts) == 2 && r.Method == "GET" {
			return s.Store.Task(ctx, id)
		}
		if len(parts) == 3 {
			switch parts[2] {
			case "events":
				if r.Method == "GET" {
					return h.events(r, "", id)
				}
			case "recover":
				if r.Method == "POST" {
					var req struct{}
					if e = decodeBody(r, &req); e != nil {
						return nil, e
					}
					return map[string]bool{"accepted": true}, s.Recover(ctx, id)
				}
			case "abandon":
				if r.Method == "POST" {
					var req struct {
						Stopped bool `json:"worker_stopped"`
					}
					if e = decodeBody(r, &req); e != nil {
						return nil, e
					}
					if !req.Stopped {
						return nil, Invalid("必须先确认原Worker已经退出")
					}
					return nil, s.Abandon(ctx, id, "administrator")
				}
			case "asset":
				if r.Method == "GET" {
					data, media, e := s.Asset(ctx, id, q.Get("key"))
					if e != nil {
						return nil, e
					}
					w.Header().Set("Content-Type", media)
					w.Header().Set("Content-Length", strconv.Itoa(len(data)))
					w.Header().Set("ETag", `"`+Digest(data)+`"`)
					_, _ = w.Write(data)
					return nil, errResponseWritten
				}
			}
		}
	}
	if path == "/logs" && r.Method == "GET" {
		return ReadSystemLogs(ctx, h.options.LokiURL, q.Get("job_id"), q.Get("start"), q.Get("end"))
	}
	return nil, problem(404, "not_found", "管理接口不存在")
}
func (h *Handler) events(r *http.Request, run, task string) (any, error) {
	f, e := ParseEventQuery(r.URL.Query(), run, task)
	if e != nil {
		return nil, e
	}
	items, e := h.service.Store.QueryEvents(r.Context(), f)
	if e != nil {
		return nil, e
	}
	more := len(items) > f.Limit
	if more {
		items = items[:f.Limit]
	}
	after, before := f.After, f.Before
	if len(items) > 0 {
		if f.Direction == "backward" {
			before = items[len(items)-1].ID
			after = items[0].ID
			for i, j := 0, len(items)-1; i < j; i, j = i+1, j-1 {
				items[i], items[j] = items[j], items[i]
			}
		} else {
			after = items[len(items)-1].ID
			before = items[0].ID
		}
	}
	return map[string]any{"items": items, "next_after": after, "next_before": before, "has_more": more, "direction": f.Direction, "source": "task_journal", "sampled_at": h.service.now()}, nil
}
func (h *Handler) internal(r *http.Request, path string) (any, error) {
	if r.Method != "POST" {
		return nil, problem(405, "method_not_allowed", "仅支持POST")
	}
	s := h.service
	ctx := r.Context()
	switch path {
	case "/register":
		var w WorkerInfo
		if e := decodeBody(r, &w); e != nil {
			return nil, e
		}
		if e := s.Store.Register(ctx, w); e != nil {
			return nil, e
		}
		mode, e := s.Store.PoolMode(ctx, w.Identity.Kind)
		return map[string]any{"ok": true, "pool_mode": mode, "accepting": w.Ready && mode == "ACCEPTING"}, e
	case "/claim":
		var req struct {
			ID         string `json:"task_id"`
			Worker     string `json:"worker_id"`
			Generation int    `json:"generation"`
		}
		if e := decodeBody(r, &req); e != nil {
			return nil, e
		}
		if !ValidID(req.ID) || !namePattern.MatchString(req.Worker) || req.Generation < 1 {
			return nil, Invalid("领取参数非法")
		}
		return s.Store.Claim(ctx, req.ID, req.Worker, req.Generation)
	case "/heartbeat":
		var p Pulse
		if e := decodeBody(r, &p); e != nil {
			return nil, e
		}
		stop, e := s.Store.Heartbeat(ctx, p)
		return map[string]any{"stop_requested": stop, "server_time": s.now()}, e
	case "/reconcile":
		var req struct {
			ID string `json:"task_id"`
		}
		if e := decodeBody(r, &req); e != nil {
			return nil, e
		}
		if !ValidID(req.ID) {
			return nil, Invalid("任务ID非法")
		}
		return nil, s.Recover(ctx, req.ID)
	case "/finish":
		var f Finish
		if e := decodeBody(r, &f); e != nil {
			return nil, e
		}
		return nil, s.Finish(ctx, f)
	default:
		return nil, problem(404, "not_found", "Worker接口不存在")
	}
}

package operations

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Fixed selector + validated UUID. The browser cannot submit arbitrary LogQL,
// upstream URLs, credentials or unbounded history windows.
func ReadSystemLogs(ctx context.Context, endpoint, id, startRaw, endRaw string) (any, error) {
	if endpoint == "" {
		return map[string]any{"status": "not_configured", "items": []any{}, "source": "loki", "message": "尚未接入系统日志；管理任务日志不依赖Loki"}, nil
	}
	if !ValidID(id) {
		return nil, Invalid("请提供任务UUID")
	}
	end := time.Now().UTC()
	start := end.Add(-time.Hour)
	var err error
	if endRaw != "" {
		end, err = time.Parse(time.RFC3339, endRaw)
		if err != nil {
			return nil, Invalid("日志结束时间非法")
		}
	}
	if startRaw != "" {
		start, err = time.Parse(time.RFC3339, startRaw)
		if err != nil {
			return nil, Invalid("日志起始时间非法")
		}
	}
	if !end.After(start) || end.Sub(start) > 6*time.Hour {
		return nil, Invalid("日志查询范围最多6小时")
	}
	target, err := url.Parse(endpoint)
	if err != nil || (target.Scheme != "http" && target.Scheme != "https") || target.Host == "" || target.User != nil {
		return nil, problem(503, "logs_configuration", "系统日志地址配置非法")
	}
	target.Path = strings.TrimSuffix(target.Path, "/") + "/loki/api/v1/query_range"
	query := url.Values{"query": {`{app="rainpulse"} | json | job_id="` + id + `"`}, "start": {strconv.FormatInt(start.UnixNano(), 10)}, "end": {strconv.FormatInt(end.UnixNano(), 10)}, "direction": {"backward"}, "limit": {"200"}}
	target.RawQuery = query.Encode()
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", target.String(), nil)
	if err != nil {
		return nil, err
	}
	client := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return nil, problem(503, "logs_unavailable", "系统日志服务无法连接；不是没有日志")
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 4*1024*1024+1))
	if err != nil || len(data) > 4*1024*1024 || response.StatusCode != 200 {
		return nil, problem(503, "logs_unavailable", "系统日志服务返回失败或过大响应")
	}
	var raw struct {
		Status string `json:"status"`
		Data   struct {
			Result []struct {
				Values [][]string `json:"values"`
			} `json:"result"`
		} `json:"data"`
	}
	if json.Unmarshal(data, &raw) != nil || raw.Status != "success" {
		return nil, problem(503, "logs_invalid", "系统日志响应格式不正确")
	}
	lines := []map[string]string{}
	truncated := false
	for _, stream := range raw.Data.Result {
		for _, v := range stream.Values {
			if len(v) != 2 {
				return nil, fmt.Errorf("invalid log record")
			}
			if len(lines) >= 200 {
				truncated = true
				break
			}
			lines = append(lines, map[string]string{"timestamp_ns": v[0], "message": Redact(v[1])})
		}
		if truncated {
			break
		}
	}
	return map[string]any{"status": "available", "source": "loki", "items": lines, "bounded": true, "possibly_truncated": truncated || len(lines) >= 200, "message": "最多200条，导出仅包含本次查询；请缩小范围获取更多上下文"}, nil
}

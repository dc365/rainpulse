package operations

import (
	"context"
	"net/url"
	"strconv"
	"strings"
)

// Event ID keysets are scoped to one run/task. All writers lock that run before
// appending events; this prevents late commits inside the scope crossing a cursor.
type EventQuery struct {
	Run, Task, Attempt, Level, Search, Direction string
	After, Before                                int64
	Limit                                        int
}

func ParseEventQuery(q url.Values, run, task string) (EventQuery, error) {
	f := EventQuery{Run: run, Task: task, Attempt: q.Get("attempt"), Level: q.Get("level"), Search: strings.TrimSpace(q.Get("q")), Direction: q.Get("direction")}
	if f.Direction == "" {
		f.Direction = "forward"
	}
	if f.Direction != "forward" && f.Direction != "backward" {
		return f, Invalid("日志方向非法")
	}
	if run == "" && task == "" {
		return f, Invalid("日志必须限定作业或任务")
	}
	if f.Attempt != "" && !ValidID(f.Attempt) {
		return f, Invalid("执行尝试ID非法")
	}
	switch f.Level {
	case "", "debug", "info", "warning", "error":
	default:
		return f, Invalid("日志级别非法")
	}
	if len([]rune(f.Search)) > 128 {
		return f, Invalid("日志关键字过长")
	}
	var e error
	f.Limit, e = integer(q.Get("limit"), 200, 500)
	if e != nil {
		return f, e
	}
	for k, p := range map[string]*int64{"after": &f.After, "before": &f.Before} {
		if v := q.Get(k); v != "" {
			*p, e = strconv.ParseInt(v, 10, 64)
			if e != nil || *p < 0 || *p > 9007199254740991 {
				return f, Invalid("日志游标非法")
			}
		}
	}
	if f.Before > 0 && f.Direction != "backward" || f.After > 0 && f.Direction != "forward" {
		return f, Invalid("日志游标与读取方向不同")
	}
	return f, nil
}
func (s *Store) QueryEvents(ctx context.Context, f EventQuery) ([]Event, error) {
	// Exact ID predicates preserve index use; optional textual filters occur
	// before LIMIT, never only against the browser's loaded buffer.
	clauses := []string{}
	args := []any{}
	add := func(expression string, value any) {
		args = append(args, value)
		clauses = append(clauses, strings.Replace(expression, "?", "$"+strconv.Itoa(len(args)), 1))
	}
	if f.Run != "" {
		add("run_id=?::uuid", f.Run)
	}
	if f.Task != "" {
		add("task_id=?::uuid", f.Task)
	}
	if f.Attempt != "" {
		add("attempt_id=?::uuid", f.Attempt)
	}
	if f.Level != "" {
		add("level=?", f.Level)
	}
	if f.Search != "" {
		add("position(lower(?) in lower(event||' '||message))>0", f.Search)
	}
	order := "ASC"
	if f.Direction == "backward" {
		order = "DESC"
		if f.Before > 0 {
			add("id<?", f.Before)
		}
	} else {
		add("id>?", f.After)
	}
	args = append(args, f.Limit+1)
	rows, e := s.DB.QueryContext(ctx, `SELECT id,run_id::text,COALESCE(task_id::text,''),COALESCE(attempt_id::text,''),COALESCE(sequence,0),level,event,message,at FROM ops_events WHERE `+strings.Join(clauses, " AND ")+` ORDER BY id `+order+` LIMIT $`+strconv.Itoa(len(args)), args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []Event{}
	for rows.Next() {
		var v Event
		if e = rows.Scan(&v.ID, &v.RunID, &v.TaskID, &v.AttemptID, &v.Sequence, &v.Level, &v.Event, &v.Message, &v.At); e != nil {
			return nil, e
		}
		out = append(out, v)
	}
	return out, rows.Err()
}

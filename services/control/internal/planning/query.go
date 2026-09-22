// Package planning contains the bounded read contract used only by the planner.
// Public history/list APIs deliberately retain their existing semantics.
package planning

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

const PageSize = 200

// Window is half-open: [Start, End). UTC normalization happens before SQL.
type Window struct{ Start, End time.Time }

// Cursor uses an immutable timestamp and unique workflow ID, not OFFSET or a
// mutable status/updated_at. Moving rows out of a stage cannot skip the next page.
type Cursor struct {
	Time time.Time
	ID   string
}

type Scope struct {
	Windows       []Window
	RadarIDs      []string
	GridID        string
	Status        string
	ExcludeQCOnly bool
	IncludeReruns bool
}

// NormalizeWindows rejects accidentally unbounded reads and merges overlapping
// windows. An explicit replay interval may be disjoint from the live window.
func NormalizeWindows(windows []Window) ([]Window, error) {
	if len(windows) == 0 || len(windows) > 16 {
		return nil, fmt.Errorf("planner requires 1..16 explicit time windows")
	}
	out := append([]Window(nil), windows...)
	for i := range out {
		out[i] = Window{out[i].Start.UTC(), out[i].End.UTC()}
		if out[i].Start.IsZero() || out[i].End.IsZero() || !out[i].End.After(out[i].Start) {
			return nil, fmt.Errorf("planner time window must have a finite start before end")
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Start.Before(out[j].Start) })
	merged := out[:0]
	for _, w := range out {
		if len(merged) == 0 || w.Start.After(merged[len(merged)-1].End) {
			merged = append(merged, w)
			continue
		}
		if w.End.After(merged[len(merged)-1].End) {
			merged[len(merged)-1].End = w.End
		}
	}
	return merged, nil
}

// Predicate returns a SQL suffix for the repository's existing SELECT/scanners.
// Table/column names below are constants, never interpolated user input.
func Predicate(kind string, scope Scope, cursor *Cursor, limit int) (string, []any, error) {
	if limit < 1 || limit > PageSize {
		return "", nil, fmt.Errorf("planner page limit must be 1..%d", PageSize)
	}
	if strings.TrimSpace(scope.Status) == "" {
		return "", nil, fmt.Errorf("planner status is required")
	}
	windows, err := NormalizeWindows(scope.Windows)
	if err != nil {
		return "", nil, err
	}
	var column, id, status string
	switch kind {
	case "radar":
		column, id, status = "s.volume_end_time", "r.run_id", "r.status"
	case "analysis":
		column, id, status = "a.analysis_time", "a.analysis_id", "a.status"
	case "forecast":
		column, id, status = "issue_time", "run_id", "status"
	default:
		return "", nil, fmt.Errorf("unknown planner query kind %q", kind)
	}
	args := []any{}
	bind := func(value any) string { args = append(args, value); return fmt.Sprintf("$%d", len(args)) }
	clauses := []string{status + " = " + bind(scope.Status)}
	timeClauses := make([]string, 0, len(windows))
	for _, w := range windows {
		timeClauses = append(timeClauses, "("+column+" >= "+bind(w.Start)+" AND "+column+" < "+bind(w.End)+")")
	}
	timeClause := "(" + strings.Join(timeClauses, " OR ") + ")"
	if scope.IncludeReruns {
		if kind != "forecast" {
			return "", nil, fmt.Errorf("rerun exemption is only valid for forecast queries")
		}
		timeClause = "(" + timeClause + " OR rerun_of IS NOT NULL)"
	}
	clauses = append(clauses, timeClause)
	switch kind {
	case "radar":
		radars := make([]string, 0, len(scope.RadarIDs))
		seen := map[string]bool{}
		for _, r := range scope.RadarIDs {
			r = strings.ToLower(strings.TrimSpace(r))
			if r != "" && !seen[r] {
				radars = append(radars, r)
				seen[r] = true
			}
		}
		if len(radars) == 0 {
			return "", nil, fmt.Errorf("planner radar allowlist cannot be empty")
		}
		sort.Strings(radars)
		clauses = append(clauses, "lower(s.radar_id) = ANY("+bind(radars)+"::text[])")
		if scope.ExcludeQCOnly {
			clauses = append(clauses, "NOT EXISTS (SELECT 1 FROM qc_batch_items b WHERE b.kind = 'qc' AND b.item_id = s.scan_id)")
		}
	case "analysis":
		if scope.GridID == "" {
			return "", nil, fmt.Errorf("planner grid_id is required")
		}
		clauses = append(clauses, "a.grid_id = "+bind(scope.GridID))
		clauses = append(clauses, "NOT EXISTS (SELECT 1 FROM mosaic_runs m JOIN jobs j ON j.job_id = m.job_id WHERE m.analysis_id = a.analysis_id AND j.regeneration_request_id IS NOT NULL)")
	case "forecast":
		if scope.GridID == "" {
			return "", nil, fmt.Errorf("planner grid_id is required")
		}
		clauses = append(clauses, "grid_id = "+bind(scope.GridID))
	}
	if cursor != nil {
		if cursor.Time.IsZero() || !validUUID(cursor.ID) {
			return "", nil, fmt.Errorf("invalid planner cursor")
		}
		clauses = append(clauses, "("+column+", "+id+") > ("+bind(cursor.Time.UTC())+", "+bind(cursor.ID)+"::uuid)")
	}
	// Oldest eligible first. Public endpoint ordering is deliberately untouched.
	return "\nWHERE " + strings.Join(clauses, "\n  AND ") + "\nORDER BY " + column + " ASC, " + id + " ASC\nLIMIT " + bind(limit), args, nil
}

func validUUID(s string) bool {
	if len(s) != 36 {
		return false
	}
	for i, r := range s {
		if i == 8 || i == 13 || i == 18 || i == 23 {
			if r != '-' {
				return false
			}
			continue
		}
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F') {
			return false
		}
	}
	return true
}

// Prune bounds only the acceleration cache. Durable job IDs and transactions
// remain authoritative, so eviction must never be treated as permission to
// replace an existing artifact or to bypass a quality/version gate.
func Prune[K comparable](items map[K]time.Time, now time.Time, ttl time.Duration, maximum int) {
	for key, at := range items {
		if !at.After(now.Add(-ttl)) {
			delete(items, key)
		}
	}
	if maximum < 0 {
		maximum = 0
	}
	if len(items) <= maximum {
		return
	}
	type entry struct {
		key K
		at  time.Time
	}
	ordered := make([]entry, 0, len(items))
	for key, at := range items {
		ordered = append(ordered, entry{key, at})
	}
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].at.Before(ordered[j].at) })
	for _, item := range ordered[:len(ordered)-maximum] {
		delete(items, item.key)
	}
}

package operations

import (
	"context"
	"encoding/json"
	"math"
	"net/url"
	"sort"
	"time"
)

const MaximumPerformanceAttempts = 10000

var performanceMetricKeys = []string{"queue_ms", "attempt_elapsed_ms", "compute_wall_ms", "publication_wall_ms", "input_read_ms", "context_ms", "qc_core_ms", "serialize_validate_ms", "process_rss_sampled_peak_bytes", "input_bytes", "output_bytes"}

type PerformanceQuery struct {
	Start, End        time.Time
	Kind, Fingerprint string
}

func ParsePerformanceQuery(q url.Values) (PerformanceQuery, error) {
	start, end, e := parseWindow(q, 7*24*time.Hour)
	p := PerformanceQuery{Start: start, End: end, Kind: q.Get("kind"), Fingerprint: q.Get("fingerprint")}
	if e != nil {
		return p, e
	}
	if p.Kind != "" && !validKind(p.Kind) {
		return p, Invalid("性能阶段非法")
	}
	if p.Fingerprint != "" && !shaPattern.MatchString(p.Fingerprint) {
		return p, Invalid("执行身份应为完整SHA256")
	}
	return p, nil
}

type MetricDistribution struct {
	Samples int      `json:"samples"`
	P50     *float64 `json:"p50"`
	P95     *float64 `json:"p95"`
	Maximum *float64 `json:"maximum"`
}
type PerformanceSample struct {
	ID          string             `json:"id"`
	TaskID      string             `json:"task_id"`
	RunID       string             `json:"run_id"`
	Kind        string             `json:"kind"`
	Fingerprint string             `json:"fingerprint"`
	WorkerID    string             `json:"worker_id"`
	State       string             `json:"state"`
	StartedAt   time.Time          `json:"started_at"`
	FinishedAt  *time.Time         `json:"finished_at"`
	QueuedAt    *time.Time         `json:"queued_at"`
	Metrics     map[string]float64 `json:"metrics"`
}
type PerformanceGroup struct {
	Kind        string                        `json:"kind"`
	Fingerprint string                        `json:"fingerprint"`
	Workers     []string                      `json:"workers"`
	Attempts    int                           `json:"attempts"`
	States      map[string]int                `json:"states"`
	Metrics     map[string]MetricDistribution `json:"metrics"`
}
type PerformanceReport struct {
	Start         time.Time           `json:"start"`
	End           time.Time           `json:"end"`
	SampledAt     time.Time           `json:"sampled_at"`
	Scope         string              `json:"scope"`
	QuantileBasis string              `json:"quantile_basis"`
	Attempts      int                 `json:"attempts"`
	Groups        []PerformanceGroup  `json:"groups"`
	Slow          []PerformanceSample `json:"slow"`
}

// Nearest rank on observed values. Zero is a measurement; absent/negative/nonfinite
// is excluded. P95 of per-worker P95s is never used.
func Distribution(values []float64) MetricDistribution {
	clean := make([]float64, 0, len(values))
	for _, v := range values {
		if v >= 0 && !math.IsNaN(v) && !math.IsInf(v, 0) {
			clean = append(clean, v)
		}
	}
	sort.Float64s(clean)
	d := MetricDistribution{Samples: len(clean)}
	if len(clean) == 0 {
		return d
	}
	quantile := func(q float64) *float64 { v := clean[int(math.Ceil(float64(len(clean))*q))-1]; return &v }
	d.P50 = quantile(.5)
	d.P95 = quantile(.95)
	d.Maximum = quantile(1)
	return d
}
func measuredTiming(a PerformanceSample) map[string]float64 {
	out := map[string]float64{}
	for _, k := range performanceMetricKeys {
		if v, ok := a.Metrics[k]; ok && v >= 0 && !math.IsNaN(v) && !math.IsInf(v, 0) {
			out[k] = v
		}
	}
	// These timings are derived exclusively from database-owned timestamps.
	delete(out, "queue_ms")
	delete(out, "attempt_elapsed_ms")
	if a.QueuedAt != nil && !a.StartedAt.Before(*a.QueuedAt) {
		out["queue_ms"] = float64(a.StartedAt.Sub(*a.QueuedAt)) / float64(time.Millisecond)
	}
	if a.FinishedAt != nil && !a.FinishedAt.Before(a.StartedAt) {
		out["attempt_elapsed_ms"] = float64(a.FinishedAt.Sub(a.StartedAt)) / float64(time.Millisecond)
	}
	return out
}
func BuildPerformance(q PerformanceQuery, samples []PerformanceSample, now time.Time) PerformanceReport {
	out := PerformanceReport{Start: q.Start, End: q.End, SampledAt: now.UTC(), Scope: "management_attempts_started_in_window", QuantileBasis: "successful_attempts_only_nearest_rank", Attempts: len(samples), Groups: []PerformanceGroup{}, Slow: []PerformanceSample{}}
	groups := map[string]*PerformanceGroup{}
	values := map[string]map[string][]float64{}
	workers := map[string]map[string]bool{}
	for _, a := range samples {
		key := a.Kind + "/" + a.Fingerprint
		g := groups[key]
		if g == nil {
			g = &PerformanceGroup{Kind: a.Kind, Fingerprint: a.Fingerprint, Workers: []string{}, States: map[string]int{}, Metrics: map[string]MetricDistribution{}}
			groups[key] = g
			values[key] = map[string][]float64{}
			workers[key] = map[string]bool{}
		}
		g.Attempts++
		g.States[a.State]++
		workers[key][a.WorkerID] = true
		if a.State != "SUCCEEDED" {
			continue
		}
		a.Metrics = measuredTiming(a)
		for k, v := range a.Metrics {
			values[key][k] = append(values[key][k], v)
		}
		if _, ok := a.Metrics["attempt_elapsed_ms"]; ok {
			out.Slow = append(out.Slow, a)
		}
	}
	keys := []string{}
	for key := range groups {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		g := groups[key]
		for w := range workers[key] {
			g.Workers = append(g.Workers, w)
		}
		sort.Strings(g.Workers)
		for _, k := range performanceMetricKeys {
			g.Metrics[k] = Distribution(values[key][k])
		}
		out.Groups = append(out.Groups, *g)
	}
	sort.Slice(out.Slow, func(i, j int) bool {
		a, b := out.Slow[i], out.Slow[j]
		if a.Metrics["attempt_elapsed_ms"] == b.Metrics["attempt_elapsed_ms"] {
			return a.ID < b.ID
		}
		return a.Metrics["attempt_elapsed_ms"] > b.Metrics["attempt_elapsed_ms"]
	})
	if len(out.Slow) > 20 {
		out.Slow = out.Slow[:20]
	}
	return out
}
func (s *Store) Performance(ctx context.Context, q PerformanceQuery) (PerformanceReport, error) {
	rows, e := s.DB.QueryContext(ctx, `SELECT a.id::text,a.task_id::text,t.run_id::text,t.kind,t.spec#>>'{identity,fingerprint}',a.worker_id,a.state,a.started_at,a.finished_at,a.queued_at,a.metrics
 FROM ops_attempts a JOIN ops_tasks t ON t.id=a.task_id
 WHERE a.started_at >= $1 AND a.started_at < $2 AND ($3='' OR t.kind=$3)
 AND ($4='' OR t.spec#>>'{identity,fingerprint}'=$4)
 ORDER BY a.started_at DESC,a.id DESC LIMIT $5`, q.Start, q.End, q.Kind, q.Fingerprint, MaximumPerformanceAttempts+1)
	if e != nil {
		return PerformanceReport{}, e
	}
	defer rows.Close()
	all := []PerformanceSample{}
	for rows.Next() {
		var a PerformanceSample
		var raw []byte
		if e = rows.Scan(&a.ID, &a.TaskID, &a.RunID, &a.Kind, &a.Fingerprint, &a.WorkerID, &a.State, &a.StartedAt, &a.FinishedAt, &a.QueuedAt, &raw); e != nil {
			return PerformanceReport{}, e
		}
		if e = json.Unmarshal(raw, &a.Metrics); e != nil {
			return PerformanceReport{}, e
		}
		all = append(all, a)
	}
	if e = rows.Err(); e != nil {
		return PerformanceReport{}, e
	}
	if len(all) > MaximumPerformanceAttempts {
		return PerformanceReport{}, problem(422, "window_too_large", "该范围超过10000次尝试，请缩小时间或选择执行身份；未返回抽样分位数")
	}
	return BuildPerformance(q, all, time.Now()), nil
}

package workspace

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"
)

const analysisJobsPath = verificationPath + "/jobs"

var analysisJobID = regexp.MustCompile(`^[a-f0-9]{32}$`)

type analysisJobInput struct {
	CycleIDs   []string `json:"cycle_ids"`
	Algorithms []string `json:"algorithms"`
	Leads      []int    `json:"leads"`
	Threshold  int      `json:"threshold"`
	WindowKM   int      `json:"window_km"`
}

func (i analysisJobInput) valid() bool {
	if len(i.CycleIDs) < 1 || len(i.CycleIDs) > 128 || len(i.Leads) < 1 || len(i.Leads) > 24 || !validAlgorithms(i.Algorithms) || !slices.Contains([]int{1, 5, 10, 20, 50}, i.Threshold) || !slices.Contains([]int{1, 5, 10, 20, 40}, i.WindowKM) {
		return false
	}
	seen := map[string]bool{}
	for _, id := range i.CycleIDs {
		if !validCycleID(id) || seen[id] {
			return false
		}
		seen[id] = true
	}
	leads := map[int]bool{}
	for _, lead := range i.Leads {
		if lead < 5 || lead > 120 || lead%5 != 0 || leads[lead] {
			return false
		}
		leads[lead] = true
	}
	return true
}

type analysisJob struct {
	ID               string           `json:"id"`
	Status           string           `json:"status"`
	Input            analysisJobInput `json:"input"`
	Started          string           `json:"started_at"`
	Completed        int              `json:"completed"`
	Total            int              `json:"total"`
	Records          []map[string]any `json:"records"`
	Summary          map[string]any   `json:"summary,omitempty"`
	Error            string           `json:"error,omitempty"`
	PersistenceError string           `json:"persistence_error,omitempty"`
}
type analysisJobs struct {
	mu     sync.Mutex
	root   string
	jobs   map[string]*analysisJob
	active string
	cancel context.CancelFunc
}

func newAnalysisJobs(root string) *analysisJobs {
	m := &analysisJobs{root: root, jobs: map[string]*analysisJob{}}
	if root != "" {
		entries, _ := os.ReadDir(root)
		for _, entry := range entries {
			id := strings.TrimSuffix(entry.Name(), ".json")
			if !analysisJobID.MatchString(id) {
				continue
			}
			info, err := entry.Info()
			if err != nil || info.Size() > 16<<20 {
				continue
			}
			data, err := os.ReadFile(filepath.Join(root, entry.Name()))
			if err != nil {
				continue
			}
			var job analysisJob
			if json.Unmarshal(data, &job) != nil || job.ID != id {
				continue
			}
			if job.Status == "running" {
				job.Status = "interrupted"
				job.Error = "服务重启中断，请重新计算"
			}
			m.jobs[id] = &job
		}
	}
	m.prune()
	return m
}
func (m *analysisJobs) prune() {
	ids := make([]string, 0, len(m.jobs))
	for id := range m.jobs {
		if id != m.active {
			ids = append(ids, id)
		}
	}
	sort.Slice(ids, func(i, j int) bool { return m.jobs[ids[i]].Started < m.jobs[ids[j]].Started })
	for len(m.jobs) > 8 && len(ids) > 0 {
		id := ids[0]
		ids = ids[1:]
		delete(m.jobs, id)
		if m.root != "" {
			_ = os.Remove(filepath.Join(m.root, id+".json"))
		}
	}
}
func (m *analysisJobs) save(job *analysisJob) {
	if m.root == "" {
		job.PersistenceError = "未配置持久化目录，重启后不保留"
		return
	}
	err := func() error {
		if err := os.MkdirAll(m.root, 0750); err != nil {
			return err
		}
		data, err := json.Marshal(job)
		if err != nil {
			return err
		}
		f, err := os.CreateTemp(m.root, ".report-*")
		if err != nil {
			return err
		}
		defer os.Remove(f.Name())
		if _, err = f.Write(data); err != nil {
			_ = f.Close()
			return err
		}
		if err = f.Sync(); err != nil {
			_ = f.Close()
			return err
		}
		if err = f.Close(); err != nil {
			return err
		}
		return os.Rename(f.Name(), filepath.Join(m.root, job.ID+".json"))
	}()
	if err != nil {
		job.PersistenceError = "统计报告保存失败，当前仅在内存中"
	} else {
		job.PersistenceError = ""
	}
}
func (h *runtimeHandler) jobManager() *analysisJobs {
	h.analysisOnce.Do(func() { h.analysis = newAnalysisJobs(os.Getenv("RAINPULSE_VERIFICATION_JOB_ROOT")) })
	return h.analysis
}
func (h *runtimeHandler) verificationJobs(w http.ResponseWriter, r *http.Request) {
	m := h.jobManager()
	if r.URL.Path == analysisJobsPath && r.Method == http.MethodPost {
		var input analysisJobInput
		if !decodeAnalysisInput(w, r, &input) {
			return
		}
		if !input.valid() {
			runtimeWriteError(w, 400, "invalid_analysis", "最多选择 128 个周期、24 个时效，使用有效算法、阈值和邻域")
			return
		}
		sort.Strings(input.CycleIDs)
		sort.Strings(input.Algorithms)
		sort.Ints(input.Leads)
		data, _ := json.Marshal(input)
		digest := sha256.Sum256(data)
		id := hex.EncodeToString(digest[:16])
		m.mu.Lock()
		defer m.mu.Unlock()
		if m.active != "" {
			analysisJSON(w, 409, map[string]any{"error": "已有检验任务运行，请等待或取消", "active_job_id": m.active})
			return
		}
		job := &analysisJob{ID: id, Status: "running", Input: input, Started: time.Now().UTC().Format(time.RFC3339), Total: len(input.CycleIDs) * len(input.Leads), Records: []map[string]any{}}
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Hour)
		m.active = id
		m.cancel = cancel
		m.jobs[id] = job
		m.prune()
		m.save(job)
		go h.runVerificationJob(ctx, m, job)
		analysisJSON(w, 202, job)
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if r.URL.Path == analysisJobsPath && r.Method == http.MethodGet {
		items := []analysisJob{}
		for _, job := range m.jobs {
			copy := *job
			copy.Records = nil
			copy.Summary = nil
			items = append(items, copy)
		}
		sort.Slice(items, func(i, j int) bool { return items[i].Started > items[j].Started })
		analysisJSON(w, 200, map[string]any{"items": items})
		return
	}
	tail := strings.TrimPrefix(r.URL.Path, analysisJobsPath+"/")
	id := strings.TrimSuffix(tail, "/cancel")
	if !analysisJobID.MatchString(id) || m.jobs[id] == nil {
		runtimeWriteError(w, 404, "not_found", "检验任务不存在")
		return
	}
	if r.Method == http.MethodPost && tail == id+"/cancel" {
		if m.active == id && m.cancel != nil {
			m.cancel()
		}
		analysisJSON(w, 202, m.jobs[id])
		return
	}
	if r.Method == http.MethodGet && tail == id {
		analysisJSON(w, 200, m.jobs[id])
		return
	}
	runtimeWriteError(w, 405, "method_not_allowed", "请求方式不支持")
}
func compactComparison(data map[string]any, input analysisJobInput) (map[string]any, error) {
	full, ok := data["metrics"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("检验结果缺少指标")
	}
	out := map[string]any{}
	for _, a := range input.Algorithms {
		metrics, ok := full[a].(map[string]any)
		if !ok {
			return nil, fmt.Errorf("检验算法不完整")
		}
		if count, ok := metrics["valid_cells"].(float64); !ok || count <= 0 {
			return nil, fmt.Errorf("所选算法没有共同有效格点，不能评分")
		}
		rows, ok := metrics["rows"].([]any)
		if !ok {
			return nil, fmt.Errorf("指标行缺失")
		}
		found := false
		for _, raw := range rows {
			row, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			if row["threshold"] != float64(input.Threshold) || row["window_km"] != float64(input.WindowKM) {
				continue
			}
			values := map[string]any{}
			for _, k := range []string{"csi", "fss", "neighborhood_csi", "neighborhood_cells"} {
				values[k] = row[k]
			}
			for _, k := range []string{"mae", "rmse", "coverage", "valid_cells"} {
				values[k] = metrics[k]
			}
			out[a] = values
			found = true
			break
		}
		if !found {
			return nil, fmt.Errorf("指定阈值/邻域指标缺失")
		}
	}
	return out, nil
}
func (h *runtimeHandler) runVerificationJob(ctx context.Context, m *analysisJobs, job *analysisJob) {
	defer func() {
		m.mu.Lock()
		defer m.mu.Unlock()
		if recovered := recover(); recovered != nil {
			job.Status = "failed"
			job.Error = "检验任务异常，请重试"
		}
		if m.cancel != nil {
			m.cancel()
		}
		m.active = ""
		m.cancel = nil
		m.save(job)
	}()
	for _, id := range job.Input.CycleIDs {
		detail, projectionErr := h.analysisDetail(ctx, id)
		for _, lead := range job.Input.Leads {
			if ctx.Err() != nil {
				m.mu.Lock()
				job.Status = "cancelled"
				job.Error = "任务已取消或达到两小时上限"
				m.mu.Unlock()
				return
			}
			record := map[string]any{"cycle_id": id, "issue_time": detail.IssueTime, "lead_minutes": lead, "status": "unavailable"}
			err := projectionErr
			if err == nil {
				var data map[string]any
				data, err = h.compareDetail(ctx, detail, comparisonInput{CycleID: id, Algorithms: job.Input.Algorithms, Lead: lead})
				if err == nil {
					var metrics map[string]any
					metrics, err = compactComparison(data, job.Input)
					if err == nil {
						record["status"] = "ready"
						record["metrics"] = metrics
						record["source_fingerprint"] = data["source_fingerprint"]
					}
				}
			}
			if err != nil {
				record["reason"] = err.Error()
			}
			m.mu.Lock()
			job.Records = append(job.Records, record)
			job.Completed++
			m.mu.Unlock()
		}
		m.mu.Lock()
		m.save(job)
		m.mu.Unlock()
	}
	summary, err := callAnalysisWorker(ctx, "/interval/summary", map[string]any{"records": job.Records, "algorithms": job.Input.Algorithms})
	m.mu.Lock()
	defer m.mu.Unlock()
	if err != nil {
		job.Status = "failed"
		job.Error = "统计汇总失败，可查看已完成记录"
	} else {
		job.Status = "complete"
		job.Summary = summary
	}
}

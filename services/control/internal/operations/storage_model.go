package operations

import (
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"strings"
	"time"
)

// Storage telemetry is host-filesystem data, never inferred from object count.
type StorageReport struct {
	ID               string    `json:"id"`
	Host             string    `json:"host"`
	Label            string    `json:"label"`
	Path             string    `json:"path"`
	SampledAt        time.Time `json:"sampled_at"`
	TotalBytes       uint64    `json:"total_bytes"`
	AvailableBytes   uint64    `json:"available_bytes"`
	InodesTotal      uint64    `json:"inodes_total"`
	InodesFree       uint64    `json:"inodes_free"`
	InodeUsedPercent *float64  `json:"inode_used_percent"`
	Device           string    `json:"device"`
}

func (r StorageReport) Validate(now time.Time) error {
	if !namePattern.MatchString(r.ID) || r.Host == "" || len(r.Host) > 128 || r.Label == "" || len(r.Label) > 128 || len(r.Path) > 1024 || !strings.HasPrefix(r.Path, "/") || len(r.Device) > 128 {
		return Invalid("主机存储采样身份非法")
	}
	if r.SampledAt.IsZero() || r.SampledAt.After(now.Add(5*time.Second)) || now.Sub(r.SampledAt) > time.Hour || r.TotalBytes == 0 || r.AvailableBytes > r.TotalBytes || r.InodesFree > r.InodesTotal {
		return Invalid("存储采样时间或容量非法")
	}
	// Clients cannot submit a flattering percentage inconsistent with counters.
	if r.InodesTotal == 0 {
		if r.InodeUsedPercent != nil {
			return Invalid("该文件系统未提供inode总量")
		}
		return nil
	}
	expected := 100 * (1 - float64(r.InodesFree)/float64(r.InodesTotal))
	if r.InodeUsedPercent == nil || math.IsNaN(*r.InodeUsedPercent) || math.IsInf(*r.InodeUsedPercent, 0) || *r.InodeUsedPercent < expected-.01 || *r.InodeUsedPercent > expected+.01 {
		return Invalid("inode百分比与计数不一致")
	}
	return nil
}
func PressureProblem(r StorageReport, now time.Time, maxAge time.Duration, inodeStop int, minFree uint64) string {
	if r.SampledAt.IsZero() || r.SampledAt.After(now.Add(5*time.Second)) || now.Sub(r.SampledAt) > maxAge {
		return "存储采样缺失或过期；暂停新的管理计算，不中断已有任务"
	}
	if r.InodeUsedPercent == nil {
		return "配置的存储门禁没有有效inode采样"
	}
	if *r.InodeUsedPercent >= float64(inodeStop) {
		return "inode余量不足；暂停新管理计算，请先检查并清理"
	}
	if r.AvailableBytes < minFree {
		return "磁盘可用空间不足；暂停新管理计算"
	}
	return ""
}

type ReleaseChannel struct {
	Kind      string    `json:"kind"`
	Current   string    `json:"current_fingerprint"`
	Previous  string    `json:"previous_fingerprint"`
	Revision  int64     `json:"revision"`
	UpdatedAt time.Time `json:"updated_at"`
}
type RetentionPolicy struct {
	KeepLatest      int `json:"keep_latest"`
	MinimumAgeHours int `json:"minimum_age_hours"`
	Limit           int `json:"limit"`
}

func (p RetentionPolicy) Validate() error {
	if p.KeepLatest < 1 || p.KeepLatest > 5 || p.MinimumAgeHours < 24 || p.MinimumAgeHours > 8760 || p.Limit < 1 || p.Limit > 20 {
		return Invalid("保留1至5份、至少24小时缓冲、每批1至20个作业")
	}
	return nil
}

type RetentionTarget struct {
	RunID        string             `json:"run_id"`
	Name         string             `json:"name"`
	UpdatedAt    time.Time          `json:"updated_at"`
	LogicalBytes int64              `json:"logical_bytes"`
	Attempts     []RetentionAttempt `json:"attempts"`
}
type RetentionAttempt struct {
	ID           string `json:"id"`
	TaskID       string `json:"task_id"`
	Kind         string `json:"kind"`
	Prefix       string `json:"prefix"`
	MarkerSHA256 string `json:"marker_sha256,omitempty"`
}
type RetentionPlan struct {
	ObjectStoreEndpoint string            `json:"object_store_endpoint"`
	ID                  string            `json:"id"`
	Policy              RetentionPolicy   `json:"policy"`
	Targets             []RetentionTarget `json:"targets"`
	CreatedAt           time.Time         `json:"created_at"`
	ExpiresAt           time.Time         `json:"expires_at"`
	Digest              string            `json:"digest"`
	Scope               string            `json:"scope"`
}

func CandidateAttemptPrefix(run, task, attempt string) (string, error) {
	if !ValidID(run) || !ValidID(task) || !ValidID(attempt) {
		return "", Invalid("候选目录身份非法")
	}
	return "s3://rainpulse/operations/" + run + "/" + task + "/attempts/" + attempt + "/", nil
}
func ValidateAttemptPrefix(run string, a RetentionAttempt) error {
	expected, e := CandidateAttemptPrefix(run, a.TaskID, a.ID)
	if e != nil {
		return e
	}
	if a.Prefix != expected || !validKind(a.Kind) || (a.MarkerSHA256 != "" && !shaPattern.MatchString(a.MarkerSHA256)) {
		return Invalid("清理仅限已登记管理尝试的精确目录")
	}
	return nil
}

// Extract references from structured frozen inputs, not substring guessing.
func CandidateRunFromURI(uri string) (string, bool) {
	u, e := url.Parse(uri)
	if e != nil || u.Scheme != "s3" || u.Host != "rainpulse" || u.RawQuery != "" || u.Fragment != "" {
		return "", false
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	return func() (string, bool) {
		if len(parts) >= 2 && parts[0] == "operations" && ValidID(parts[1]) {
			return parts[1], true
		}
		return "", false
	}()
}
func (p RetentionPlan) Validate(now time.Time) error {
	if !now.Before(p.ExpiresAt) {
		return Invalid("清理计划已过期")
	}
	return p.ValidateIntegrity()
}

// Resuming an irrevocably retired plan ignores expiry, never identity or scope.
func (p RetentionPlan) ValidateIntegrity() error {
	u, e := url.Parse(p.ObjectStoreEndpoint)
	if e != nil || u.Host == "" || u.User != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
		return Invalid("清理计划对象存储身份非法")
	}
	if !ValidID(p.ID) || p.Scope != "managed_candidates_only" || p.Policy.Validate() != nil || len(p.Targets) > p.Policy.Limit || p.CreatedAt.IsZero() || !p.CreatedAt.Before(p.ExpiresAt) {
		return Invalid("清理计划非法或已过期")
	}
	seen := map[string]bool{}
	for _, t := range p.Targets {
		if !ValidID(t.RunID) || seen[t.RunID] || len(t.Attempts) == 0 || len(t.Attempts) > 2048 {
			return Invalid("清理目标重复或缺少尝试")
		}
		seen[t.RunID] = true
		for _, a := range t.Attempts {
			if seen[a.Prefix] {
				return Invalid("清理尝试目录重复")
			}
			seen[a.Prefix] = true
			if e := ValidateAttemptPrefix(t.RunID, a); e != nil {
				return e
			}
		}
	}
	d := p.Digest
	p.Digest = ""
	if !shaPattern.MatchString(d) || CanonicalDigest(p) != d {
		return Invalid("清理计划摘要不一致")
	}
	return nil
}
func decodeRetention(raw []byte) (RetentionPlan, error) {
	var p RetentionPlan
	e := json.Unmarshal(raw, &p)
	return p, e
}
func managedPrefix(run string) string { return fmt.Sprintf("s3://rainpulse/operations/%s/", run) }

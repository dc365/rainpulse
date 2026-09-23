// Package operations owns bounded administrative reruns and their audit trail.
// It has no dependency on HTTP handlers, numerical algorithms, or ORM frameworks.
package operations

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"time"
)

const Version = "1.0"
const MaxTasks = 128
const WorkerFreshness = 75 * time.Second
const LeaseDuration = 120 * time.Second

var uuidPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var shaPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var namePattern = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,96}$`)

func NewID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	s := hex.EncodeToString(b[:])
	return s[:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:]
}
func Digest(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func JSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return b
}

// CanonicalDigest normalizes nested RawMessage objects before hashing. PostgreSQL
// jsonb changes key order/whitespace; a byte hash of re-marshaled RawMessage would
// reject an otherwise unchanged persisted plan on every submit.
func CanonicalDigest(v any) string {
	raw := JSON(v)
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.UseNumber()
	var normalized any
	if err := decoder.Decode(&normalized); err != nil {
		panic(err)
	}
	return Digest(JSON(normalized))
}
func ValidID(s string) bool { return uuidPattern.MatchString(s) }

type Problem struct {
	Status  int    `json:"-"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

func (e *Problem) Error() string                     { return e.Code + ": " + e.Message }
func problem(status int, code, message string) error { return &Problem{status, code, message} }
func Conflict(s string) error                        { return problem(409, "conflict", s) }
func Invalid(s string) error                         { return problem(422, "invalid_request", s) }

var ErrNotFound = &Problem{404, "not_found", "记录不存在"}
var ErrUnavailable = &Problem{503, "unavailable", "管理服务不可用；请检查数据库初始化与服务日志"}

type Selection struct {
	Preset       string    `json:"preset"`
	Start        time.Time `json:"start"`
	End          time.Time `json:"end"`
	RadarIDs     []string  `json:"radar_ids"`
	SourceJobIDs []string  `json:"source_job_ids,omitempty"`
	Name         string    `json:"name"`
}

func (s Selection) Validate() error {
	if len([]rune(s.Name)) > 120 {
		return Invalid("名称过长")
	}
	switch s.Preset {
	case "qc_preview", "render_only":
		if s.Start.IsZero() || s.End.IsZero() || !s.End.After(s.Start) || s.End.Sub(s.Start) > 24*time.Hour {
			return Invalid("请选择不超过24小时的半开时间范围")
		}
		if len(s.RadarIDs) < 1 || len(s.RadarIDs) > 16 {
			return Invalid("请选择1至16个雷达站")
		}
		seen := map[string]bool{}
		for _, id := range s.RadarIDs {
			key := strings.ToLower(id)
			if !namePattern.MatchString(id) || seen[key] {
				return Invalid("站号非法或重复")
			}
			seen[key] = true
		}
	case "diagnostics":
		if len(s.SourceJobIDs) == 0 || len(s.SourceJobIDs) > 32 {
			return Invalid("请选择1至32个诊断任务")
		}
		seen := map[string]bool{}
		for _, id := range s.SourceJobIDs {
			if !ValidID(id) || seen[id] {
				return Invalid("任务ID非法或重复")
			}
			seen[id] = true
		}
	default:
		return Invalid("首版支持QC与对照图、仅重建对照图、已有区域诊断任务重建")
	}
	return nil
}

type Check struct {
	Code    string `json:"code"`
	State   string `json:"state"`
	Message string `json:"message"`
	Target  string `json:"target,omitempty"`
}
type AssetRef struct {
	URI          string `json:"uri"`
	MarkerSHA256 string `json:"marker_sha256"`
	SHA256       string `json:"sha256"`
	SizeBytes    int64  `json:"size_bytes"`
	Verification string `json:"verification"`
}
type Identity struct {
	Kind        string            `json:"kind"`
	Fingerprint string            `json:"fingerprint"`
	Files       map[string]string `json:"files"`
	Versions    map[string]string `json:"versions"`
	CodeSHA256  string            `json:"code_sha256"`
}

func (i Identity) Validate() error {
	if !validKind(i.Kind) || !shaPattern.MatchString(i.Fingerprint) || !shaPattern.MatchString(i.CodeSHA256) || len(i.Files) > 512 {
		return Invalid("Worker身份不完整")
	}
	for k, v := range i.Files {
		if len(k) > 256 || !shaPattern.MatchString(v) {
			return Invalid("Worker配置身份非法")
		}
	}
	return nil
}
func validKind(k string) bool { return k == "qc" || k == "render" || k == "diagnostics" }

type Spec struct {
	ID          string          `json:"id"`
	ParentID    string          `json:"parent_id,omitempty"`
	Kind        string          `json:"kind"`
	Name        string          `json:"name"`
	SourceJobID string          `json:"source_job_id,omitempty"`
	InputURIs   []string        `json:"input_uris"`
	Inputs      []AssetRef      `json:"inputs"`
	Identity    Identity        `json:"identity"`
	Request     json.RawMessage `json:"request"`
}
type Plan struct {
	ID          string    `json:"id"`
	RunID       string    `json:"run_id"`
	Selection   Selection `json:"selection"`
	Checks      []Check   `json:"checks"`
	Tasks       []Spec    `json:"tasks"`
	Digest      string    `json:"digest"`
	CreatedAt   time.Time `json:"created_at"`
	ExpiresAt   time.Time `json:"expires_at"`
	Impact      string    `json:"impact"`
	Submittable bool      `json:"submittable"`
}

func (p *Plan) Seal(now time.Time) error {
	// Stable empty collections keep blocked plans usable by the browser.
	if p.Tasks == nil {
		p.Tasks = []Spec{}
	}
	if p.Checks == nil {
		p.Checks = []Check{}
	}
	if len(p.Tasks) == 0 {
		p.Checks = append(p.Checks, Check{"no_data", "BLOCK", "该范围没有可处理数据", ""})
	}
	if len(p.Tasks) > MaxTasks {
		return Invalid("单次最多128个阶段任务；请缩小时间范围")
	}
	seen := map[string]bool{}
	for _, s := range p.Tasks {
		if !ValidID(s.ID) || seen[s.ID] || !validKind(s.Kind) || !json.Valid(s.Request) {
			return Invalid("计划任务身份非法")
		}
		if s.ParentID != "" && !seen[s.ParentID] {
			return Invalid("依赖必须位于同一计划且排在子任务之前")
		}
		seen[s.ID] = true
	}
	p.Submittable = true
	for _, c := range p.Checks {
		if c.State == "BLOCK" {
			p.Submittable = false
		}
	}
	p.CreatedAt = now.UTC()
	p.ExpiresAt = p.CreatedAt.Add(15 * time.Minute)
	p.Digest = ""
	p.Digest = CanonicalDigest(p)
	return nil
}

type Task struct {
	StorageState   string          `json:"storage_state,omitempty"`
	ID             string          `json:"id"`
	RunID          string          `json:"run_id"`
	Spec           Spec            `json:"spec"`
	State          string          `json:"state"`
	Generation     int             `json:"generation"`
	AttemptNo      int             `json:"attempt_no"`
	CurrentAttempt string          `json:"current_attempt,omitempty"`
	ErrorCode      string          `json:"error_code"`
	ErrorMessage   string          `json:"error_message"`
	CreatedAt      time.Time       `json:"created_at"`
	DispatchedAt   *time.Time      `json:"dispatched_at"`
	QueuedAt       *time.Time      `json:"queued_at"`
	UpdatedAt      time.Time       `json:"updated_at"`
	Result         json.RawMessage `json:"result,omitempty"`
	Attempts       []Attempt       `json:"attempts,omitempty"`
	Actions        []string        `json:"actions,omitempty"`
	Stalled        bool            `json:"stalled"`
}
type Attempt struct {
	ID          string             `json:"id"`
	TaskID      string             `json:"task_id"`
	Number      int                `json:"number"`
	WorkerID    string             `json:"worker_id"`
	State       string             `json:"state"`
	Stage       string             `json:"stage"`
	StartedAt   time.Time          `json:"started_at"`
	QueuedAt    *time.Time         `json:"queued_at"`
	HeartbeatAt time.Time          `json:"heartbeat_at"`
	LeaseUntil  time.Time          `json:"lease_until"`
	FinishedAt  *time.Time         `json:"finished_at"`
	Request     json.RawMessage    `json:"request"`
	Result      json.RawMessage    `json:"result,omitempty"`
	Metrics     map[string]float64 `json:"metrics"`
}
type Run struct {
	StorageState string         `json:"storage_state,omitempty"`
	ID           string         `json:"id"`
	PlanID       string         `json:"plan_id"`
	Name         string         `json:"name"`
	Mode         string         `json:"mode"`
	State        string         `json:"state"`
	Actor        string         `json:"actor"`
	Impact       string         `json:"impact"`
	CreatedAt    time.Time      `json:"created_at"`
	UpdatedAt    time.Time      `json:"updated_at"`
	Counts       map[string]int `json:"counts"`
	Stalled      bool           `json:"stalled"`
	Tasks        []Task         `json:"tasks,omitempty"`
	Actions      []string       `json:"actions"`
}
type WorkerInfo struct {
	ID          string    `json:"id"`
	Identity    Identity  `json:"identity"`
	SeenAt      time.Time `json:"seen_at"`
	Busy        bool      `json:"busy"`
	Ready       bool      `json:"ready"`
	CurrentTask string    `json:"current_task,omitempty"`
	PoolMode    string    `json:"pool_mode,omitempty"`
}
type Event struct {
	ID        int64     `json:"id"`
	RunID     string    `json:"run_id"`
	TaskID    string    `json:"task_id,omitempty"`
	AttemptID string    `json:"attempt_id,omitempty"`
	Sequence  int64     `json:"sequence,omitempty"`
	Level     string    `json:"level"`
	Event     string    `json:"event"`
	Message   string    `json:"message"`
	At        time.Time `json:"at"`
}
type LogLine struct {
	Sequence int64  `json:"sequence"`
	Level    string `json:"level"`
	Event    string `json:"event"`
	Message  string `json:"message"`
}
type Pulse struct {
	TaskID    string             `json:"task_id"`
	AttemptID string             `json:"attempt_id"`
	Token     string             `json:"token"`
	Stage     string             `json:"stage"`
	Lines     []LogLine          `json:"lines,omitempty"`
	Metrics   map[string]float64 `json:"metrics,omitempty"`
}
type Finish struct {
	Pulse
	Outcome      string `json:"outcome"`
	ErrorCode    string `json:"error_code,omitempty"`
	ErrorMessage string `json:"error_message,omitempty"`
}
type Claim struct {
	Decision  string          `json:"decision"`
	TaskID    string          `json:"task_id,omitempty"`
	AttemptID string          `json:"attempt_id,omitempty"`
	Token     string          `json:"token,omitempty"`
	Request   json.RawMessage `json:"request,omitempty"`
	Kind      string          `json:"kind,omitempty"`
	Identity  *Identity       `json:"identity,omitempty"`
	Inputs    []AssetRef      `json:"inputs,omitempty"`
}

func IsTerminal(s string) bool {
	return s == "SUCCEEDED" || s == "FAILED" || s == "BLOCKED" || s == "CANCELLED"
}
func Aggregate(mode string, counts map[string]int) string {
	active := counts["RUNNING"] + counts["COMMITTING"]
	pending := counts["QUEUED"] + counts["WAITING"]
	if mode == "CANCELLED" {
		if active > 0 {
			return "CANCELLING"
		}
		return "CANCELLED"
	}
	if mode == "PAUSED" {
		return "PAUSED"
	}
	if active > 0 {
		return "RUNNING"
	}
	if pending > 0 {
		return "QUEUED"
	}
	if counts["FAILED"]+counts["BLOCKED"] > 0 {
		if counts["SUCCEEDED"] > 0 {
			return "PARTIAL_SUCCESS"
		}
		return "FAILED"
	}
	if counts["SUCCEEDED"] > 0 {
		return "SUCCEEDED"
	}
	return "QUEUED"
}
func AllowedActions(mode, state string) []string {
	if mode == "CANCELLED" {
		return []string{}
	}
	if mode == "PAUSED" {
		return []string{"resume", "cancel"}
	}
	if state == "FAILED" || state == "PARTIAL_SUCCESS" {
		return []string{"retry_failed", "cancel"}
	}
	if state == "SUCCEEDED" {
		return []string{}
	}
	return []string{"pause", "cancel"}
}
func (p Pulse) Validate() error {
	if !ValidID(p.TaskID) || !ValidID(p.AttemptID) || len(p.Token) != 64 || len(p.Lines) > 20 {
		return Invalid("执行回执身份或日志条数非法")
	}
	switch p.Stage {
	case "VERIFY_INPUT", "COMPUTE", "UPLOAD", "COMMIT", "RECOVER":
	default:
		return Invalid("执行阶段非法")
	}
	if len(p.Metrics) > 32 {
		return Invalid("性能字段过多")
	}
	for k, v := range p.Metrics {
		if !namePattern.MatchString(k) || math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
			return Invalid("性能字段非法")
		}
	}
	for _, l := range p.Lines {
		if l.Sequence < 1 || len(l.Event) > 64 || len([]rune(l.Message)) > 4096 || !(l.Level == "info" || l.Level == "warning" || l.Level == "error" || l.Level == "debug") {
			return Invalid("日志格式非法")
		}
	}
	return nil
}
func stableStrings(v []string) []string {
	out := append([]string(nil), v...)
	sort.Strings(out)
	return out
}
func errorStatus(err error) (int, string, string) {
	var p *Problem
	if errors.As(err, &p) {
		return p.Status, p.Code, p.Message
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return 504, "timeout", "请求超时；已受理操作可通过任务中心查询"
	}
	return http.StatusServiceUnavailable, "unavailable", ErrUnavailable.Message
}
func wrapError(err error) error {
	if err == nil {
		return nil
	}
	return fmt.Errorf("operations storage: %w", err)
}

package operations

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"path"
	"strings"
	"time"
)

const markerLimit int64 = 16 * 1024 * 1024
const previewLimit int64 = 8 * 1024 * 1024

type ObjectEntry struct {
	Key       string `json:"key"`
	SHA256    string `json:"sha256"`
	SizeBytes int64  `json:"size_bytes"`
}
type Marker struct {
	SchemaVersion string            `json:"schema_version"`
	SHA256        string            `json:"sha256"`
	SizeBytes     int64             `json:"size_bytes"`
	DataPrefix    string            `json:"data_prefix"`
	Objects       []ObjectEntry     `json:"objects"`
	PackedEntries []json.RawMessage `json:"packed_entries,omitempty"`
	Completion    json.RawMessage   `json:"completion_event"`
}
type Candidate struct {
	Asset         AssetRef        `json:"asset"`
	EventID       string          `json:"event_id"`
	StartedAt     time.Time       `json:"started_at"`
	FinishedAt    time.Time       `json:"finished_at"`
	RuntimeMS     int64           `json:"runtime_ms"`
	CandidateOnly bool            `json:"candidate_only"`
	Summary       json.RawMessage `json:"summary,omitempty"`
}

func safeKey(key string) bool {
	return key != "" && !strings.HasPrefix(key, "/") && path.Clean(key) == key && !strings.ContainsAny(key, "\\\x00?#") && key != ".." && !strings.HasPrefix(key, "../")
}
func validAssetURI(uri string) bool {
	u, err := url.Parse(uri)
	return err == nil && u.Scheme == "s3" && u.Host != "" && u.User == nil && u.RawQuery == "" && u.Fragment == "" && safeKey(strings.TrimPrefix(strings.TrimSuffix(u.Path, "/"), "/"))
}
func ParseMarker(raw []byte) (Marker, error) {
	var m Marker
	if len(raw) > int(markerLimit) {
		return m, Invalid("完成标记过大")
	}
	if err := json.Unmarshal(raw, &m); err != nil {
		return m, err
	}
	if (m.SchemaVersion != "1.0" && m.SchemaVersion != "2.0" && m.SchemaVersion != "3.0") || !shaPattern.MatchString(m.SHA256) || m.SizeBytes < 0 || m.SizeBytes > 2*1024*1024*1024 || len(m.Objects) == 0 || len(m.Objects) > 100000 {
		return m, Invalid("完成标记版本、大小或对象清单非法")
	}
	if m.DataPrefix != "" && !safeKey(m.DataPrefix) {
		return m, Invalid("完成标记路径非法")
	}
	seen := map[string]bool{}
	var size int64
	for _, o := range m.Objects {
		if !safeKey(o.Key) || seen[o.Key] || !shaPattern.MatchString(o.SHA256) || o.SizeBytes < 0 || o.SizeBytes > 2*1024*1024*1024 {
			return m, Invalid("对象清单重复或非法")
		}
		seen[o.Key] = true
		size += o.SizeBytes
	}
	if size != m.SizeBytes {
		return m, Invalid("完成标记大小不一致")
	}
	return m, nil
}
func (s *Service) marker(ctx context.Context, uri string) (Marker, AssetRef, error) {
	if !validAssetURI(uri) {
		return Marker{}, AssetRef{}, Invalid("资产URI非法")
	}
	ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	data, _, err := s.Objects.ReadObject(ctx, strings.TrimSuffix(uri, "/")+"/_SUCCESS.json", markerLimit)
	if err != nil {
		return Marker{}, AssetRef{}, err
	}
	m, err := ParseMarker(data)
	ref := AssetRef{URI: strings.TrimSuffix(uri, "/"), SHA256: m.SHA256, SizeBytes: m.SizeBytes, MarkerSHA256: Digest(data), Verification: "committed_marker_checked"}
	return m, ref, err
}
func (s *Service) Probe(ctx context.Context, uri string) (AssetRef, error) {
	_, ref, err := s.marker(ctx, uri)
	return ref, err
}
func (s *Service) candidate(ctx context.Context, request []byte, kind string) (Candidate, error) {
	uri, err := s.requestURI(request, kind)
	if err != nil {
		return Candidate{}, err
	}
	m, ref, err := s.marker(ctx, uri)
	if err != nil {
		return Candidate{}, err
	}
	return VerifyCandidate(request, m, ref)
}
func VerifyCandidate(request []byte, m Marker, ref AssetRef) (Candidate, error) {
	var e struct {
		EventID   string `json:"event_id"`
		EventType string `json:"event_type"`
		JobID     string `json:"job_id"`
		RunID     string `json:"run_id"`
		TraceID   string `json:"trace_id"`
		Payload   struct {
			Status     string    `json:"status"`
			StartedAt  time.Time `json:"started_at"`
			FinishedAt time.Time `json:"finished_at"`
			RuntimeMS  int64     `json:"runtime_ms"`
			Assets     []struct {
				URI       string `json:"uri"`
				SHA256    string `json:"sha256"`
				SizeBytes int64  `json:"size_bytes"`
			} `json:"assets"`
			Diagnostics json.RawMessage `json:"diagnostics"`
		} `json:"payload"`
	}
	if err := json.Unmarshal(m.Completion, &e); err != nil {
		return Candidate{}, err
	}
	req, _, err := decodeRequest(request)
	if err != nil {
		return Candidate{}, err
	}
	if e.EventType != "job.completed" || !ValidID(e.EventID) || e.JobID != jsonString(req["job_id"]) || e.RunID != jsonString(req["run_id"]) || e.TraceID != jsonString(req["trace_id"]) || e.Payload.Status != "succeeded" || len(e.Payload.Assets) != 1 || e.Payload.StartedAt.IsZero() || e.Payload.FinishedAt.Before(e.Payload.StartedAt) || e.Payload.RuntimeMS < 0 {
		return Candidate{}, Conflict("已提交结果不属于此执行尝试")
	}
	a := e.Payload.Assets[0]
	if a.URI != ref.URI || a.SHA256 != ref.SHA256 || a.SizeBytes != ref.SizeBytes {
		return Candidate{}, Conflict("完成事件与资产标记身份不同")
	}
	summary := e.Payload.Diagnostics
	if len(summary) > 64*1024 {
		summary = JSON(map[string]any{"detail": "摘要过大，完整诊断保存在候选资产中"})
	}
	return Candidate{Asset: ref, EventID: e.EventID, StartedAt: e.Payload.StartedAt, FinishedAt: e.Payload.FinishedAt, RuntimeMS: e.Payload.RuntimeMS, CandidateOnly: true, Summary: summary}, nil
}
func (s *Service) Asset(ctx context.Context, id, key string) ([]byte, string, error) {
	if !safeKey(key) {
		return nil, "", Invalid("对象路径非法")
	}
	ext := path.Ext(key)
	if ext != ".png" && ext != ".json" {
		return nil, "", Invalid("首版只提供清单中的PNG和JSON预览")
	}
	t, err := s.Store.Task(ctx, id)
	if err != nil {
		return nil, "", err
	}
	var c Candidate
	if err = json.Unmarshal(t.Result, &c); err != nil || c.Asset.URI == "" {
		return nil, "", ErrNotFound
	}
	m, ref, err := s.marker(ctx, c.Asset.URI)
	if err != nil {
		return nil, "", err
	}
	if ref.MarkerSHA256 != c.Asset.MarkerSHA256 {
		return nil, "", Conflict("结果完成标记已变化")
	}
	if m.SchemaVersion == "3.0" {
		return nil, "", problem(422, "packed_preview_unavailable", "该旧式打包资产请通过校验工具读取；不绕过整包校验")
	}
	for _, o := range m.Objects {
		if o.Key == key {
			if o.SizeBytes > previewLimit {
				return nil, "", problem(413, "preview_too_large", "预览对象超过8 MiB，请使用离线校验工具")
			}
			objectURI := ref.URI + "/"
			if m.DataPrefix != "" {
				objectURI += m.DataPrefix + "/"
			}
			objectURI += o.Key
			data, _, err := s.Objects.ReadObject(ctx, objectURI, o.SizeBytes)
			if err != nil {
				return nil, "", err
			}
			if int64(len(data)) != o.SizeBytes || Digest(data) != o.SHA256 {
				return nil, "", Conflict("对象大小或摘要校验失败")
			}
			media := "application/json"
			if ext == ".png" {
				if len(data) < 8 || string(data[:8]) != "\x89PNG\r\n\x1a\n" {
					return nil, "", fmt.Errorf("invalid PNG")
				}
				media = "image/png"
			} else if !json.Valid(data) {
				return nil, "", fmt.Errorf("invalid JSON")
			}
			return data, media, nil
		}
	}
	return nil, "", ErrNotFound
}

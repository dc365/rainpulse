package operations

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

type Builder interface {
	Build(context.Context, Selection) ([]Spec, []Check, error)
	ValidateConfiguration(context.Context, Spec) error
}
type ObjectReader interface {
	ReadObject(context.Context, string, int64) ([]byte, string, error)
}
type Service struct {
	Store   *Store
	Builder Builder
	Objects ObjectReader
	Now     func() time.Time
}

func (s *Service) now() time.Time {
	if s.Now != nil {
		return s.Now().UTC()
	}
	return time.Now().UTC()
}

func (s *Service) Preflight(ctx context.Context, selection Selection) (Plan, error) {
	var p Plan
	if err := selection.Validate(); err != nil {
		return p, err
	}
	if err := s.Store.Ready(ctx); err != nil {
		return p, err
	}
	specs, checks, err := s.Builder.Build(ctx, selection)
	if err != nil {
		return p, err
	}
	p = Plan{ID: NewID(), RunID: NewID(), Selection: selection, Tasks: specs, Checks: checks, Impact: "仅生成独立候选QC/对照图/诊断资产；不更新自动链路、格点、拼图、QPE、预报或默认展示。成功不代表获得业务资格。"}
	if p.Selection.Name == "" {
		at := selection.Start.UTC()
		if selection.Preset == "diagnostics" {
			at = s.now()
		}
		p.Selection.Name = selection.Preset + " · " + at.Format("01-02 15:04 UTC")
	}
	workers, err := s.Store.Workers(ctx)
	if err != nil {
		return p, err
	}
	probed := map[string]AssetRef{}
	problems := map[string]error{}
	for i := range p.Tasks {
		spec := &p.Tasks[i]
		identity, e := MatchWorker(spec.Identity, workers, s.now())
		if e != nil {
			p.Checks = append(p.Checks, Check{"worker", "BLOCK", e.Error(), spec.ID})
		} else {
			spec.Identity = identity
			p.Checks = append(p.Checks, Check{"worker", "PASS", "匹配到已登记的执行能力与配置摘要", spec.ID})
		}
		spec.Inputs = []AssetRef{}
		for _, uri := range spec.InputURIs {
			ref, exists := probed[uri]
			e := problems[uri]
			if !exists && e == nil {
				ref, e = s.Probe(ctx, uri)
				if e != nil {
					problems[uri] = e
				} else {
					probed[uri] = ref
				}
			}
			if e != nil {
				p.Checks = append(p.Checks, Check{"input_marker", "BLOCK", "输入完成标记不可用或不一致；未执行算法", uri})
			} else {
				spec.Inputs = append(spec.Inputs, ref)
			}
		}
	}
	p.Checks = append(p.Checks, Check{"verification_scope", "WARN", "预检核对目录与完成标记，不等同于全数组复验；执行前由Worker逐对象完整校验输入。", ""})
	if err = p.Seal(s.now()); err != nil {
		return p, err
	}
	return p, s.Store.SavePlan(ctx, p)
}
func MatchWorker(expected Identity, workers []WorkerInfo, now time.Time) (Identity, error) {
	matches := map[string]Identity{}
	for _, w := range workers {
		if !w.Ready || now.Sub(w.SeenAt) > WorkerFreshness || w.SeenAt.After(now.Add(5*time.Second)) || w.Identity.Kind != expected.Kind {
			continue
		}
		ok := true
		for k, v := range expected.Files {
			if w.Identity.Files[k] != v {
				ok = false
			}
		}
		for k, v := range expected.Versions {
			if w.Identity.Versions[k] != v {
				ok = false
			}
		}
		if expected.Fingerprint != "" && expected.Fingerprint != w.Identity.Fingerprint {
			ok = false
		}
		if ok {
			matches[w.Identity.Fingerprint] = w.Identity
		}
	}
	if len(matches) == 0 {
		return Identity{}, Conflict("没有新鲜且配置匹配的管理Worker；请检查资源与配置，不能盲目入队")
	}
	if len(matches) != 1 {
		return Identity{}, Conflict("存在多种执行身份；请先将管理Worker版本收敛")
	}
	for _, v := range matches {
		return v, nil
	}
	panic("unreachable")
}
func (s *Service) Recheck(ctx context.Context, specs []Spec) error {
	workers, err := s.Store.Workers(ctx)
	if err != nil {
		return err
	}
	seen := map[string]AssetRef{}
	for _, spec := range specs {
		if err = s.Builder.ValidateConfiguration(ctx, spec); err != nil {
			return Conflict("当前生效配置已改变，请重新预检查：" + err.Error())
		}
		if _, err = MatchWorker(spec.Identity, workers, s.now()); err != nil {
			return err
		}
		for _, old := range spec.Inputs {
			current, ok := seen[old.URI]
			if !ok {
				current, err = s.Probe(ctx, old.URI)
				if err != nil {
					return Conflict("冻结输入已不可用，请重新预检查")
				}
				seen[old.URI] = current
			}
			if current.MarkerSHA256 != old.MarkerSHA256 || current.SHA256 != old.SHA256 || current.SizeBytes != old.SizeBytes {
				return Conflict("输入完成标记已改变，请重新预检查")
			}
		}
	}
	return nil
}
func (s *Service) Submit(ctx context.Context, id, key, actor string) (string, error) {
	if len(key) < 16 || len(key) > 128 || strings.ContainsAny(key, "\r\n") {
		return "", Invalid("幂等键长度应为16至128字符")
	}
	prior, err := s.Store.SubmittedRun(ctx, id)
	if err != nil {
		return "", err
	}
	if prior != "" { // Still validate cross-plan key reuse in the locked store method.
		p, err := s.Store.Plan(ctx, id)
		if err != nil {
			return "", err
		}
		return s.Store.Submit(ctx, p, key, actor)
	}
	p, err := s.Store.Plan(ctx, id)
	if err != nil {
		return "", err
	}
	if !p.Submittable || !s.now().Before(p.ExpiresAt) {
		return "", Conflict("计划受阻或已过期，请重新预检查")
	}
	digest := p.Digest
	p.Digest = ""
	actual := CanonicalDigest(p)
	p.Digest = digest
	if digest != actual {
		return "", Conflict("计划摘要不一致")
	}
	if err = s.Recheck(ctx, p.Tasks); err != nil {
		return "", err
	}
	return s.Store.Submit(ctx, p, key, actor)
}
func (s *Service) Action(ctx context.Context, id, action, actor string) error {
	if action == "retry_failed" || action == "resume" || action == "wake" {
		run, err := s.Store.Run(ctx, id)
		if err != nil {
			return err
		}
		specs := []Spec{}
		for _, t := range run.Tasks {
			if (action == "retry_failed" && (t.State == "FAILED" || t.State == "BLOCKED")) || (action != "retry_failed" && !IsTerminal(t.State)) {
				specs = append(specs, t.Spec)
			}
		}
		if err = s.Recheck(ctx, specs); err != nil {
			return err
		}
	}
	if action == "wake" {
		return s.Store.Wake(ctx, id)
	}
	return s.Store.Action(ctx, id, action, actor)
}
func (s *Service) Finish(ctx context.Context, f Finish) error {
	if err := f.Pulse.Validate(); err != nil {
		return err
	}
	if f.Outcome != "SUCCEEDED" && f.Outcome != "FAILED" && f.Outcome != "BLOCKED" && f.Outcome != "CANCELLED" {
		return Invalid("完成状态非法")
	}
	if len(f.ErrorCode) > 96 || len([]rune(f.ErrorMessage)) > 2048 {
		return Invalid("错误摘要过长")
	}
	var result *Candidate
	if f.Outcome == "SUCCEEDED" {
		t, err := s.Store.Task(ctx, f.TaskID)
		if err != nil {
			return err
		}
		if t.CurrentAttempt != f.AttemptID {
			return Conflict("完成消息属于旧尝试")
		}
		if len(t.Attempts) == 0 {
			return Conflict("没有执行尝试")
		}
		a := t.Attempts[len(t.Attempts)-1]
		// No request-supplied URI is ever trusted by this endpoint.
		c, err := s.candidate(ctx, a.Request, t.Spec.Kind)
		if err != nil {
			return err
		}
		result = &c
	}
	return s.Store.Finish(ctx, f, result, false)
}
func (s *Service) Recover(ctx context.Context, id string) error {
	t, err := s.Store.Task(ctx, id)
	if err != nil {
		return err
	}
	if t.State == "SUCCEEDED" {
		return nil
	}
	if t.CurrentAttempt == "" || len(t.Attempts) == 0 {
		return Conflict("没有可恢复的已领取尝试")
	}
	if IsTerminal(t.State) {
		return Conflict("该尝试已经终结；请使用失败项重试")
	}
	a := t.Attempts[len(t.Attempts)-1]
	c, err := s.candidate(ctx, a.Request, t.Spec.Kind)
	if err != nil {
		return Conflict("没有通过身份核验的已提交结果，未重新运行算法")
	}
	return s.Store.Finish(ctx, Finish{Pulse: Pulse{TaskID: id, AttemptID: a.ID, Stage: "RECOVER", Metrics: map[string]float64{}}, Outcome: "SUCCEEDED"}, &c, true)
}
func (s *Service) Abandon(ctx context.Context, id, actor string) error {
	// First attempt registration recovery: do not discard a known complete artifact.
	t, err := s.Store.Task(ctx, id)
	if err != nil {
		return err
	}
	if len(t.Attempts) > 0 {
		a := t.Attempts[len(t.Attempts)-1]
		if _, e := s.candidate(ctx, a.Request, t.Spec.Kind); e == nil {
			return Conflict("已发现完成标记，请先恢复结果登记")
		}
	}
	return s.Store.Abandon(ctx, id, actor)
}
func decodeRequest(raw []byte) (map[string]json.RawMessage, map[string]json.RawMessage, error) {
	var req, payload map[string]json.RawMessage
	if err := json.Unmarshal(raw, &req); err != nil {
		return nil, nil, err
	}
	if err := json.Unmarshal(req["payload"], &payload); err != nil {
		return nil, nil, err
	}
	return req, payload, nil
}
func jsonString(v json.RawMessage) string { var s string; _ = json.Unmarshal(v, &s); return s }
func (s *Service) requestURI(raw []byte, kind string) (string, error) {
	_, p, err := decodeRequest(raw)
	if err != nil {
		return "", err
	}
	prefix := jsonString(p["output_prefix"])
	if !strings.HasPrefix(prefix, "s3://rainpulse/operations/") {
		return "", fmt.Errorf("invalid managed output prefix")
	}
	return strings.TrimSuffix(prefix, "/") + "/" + ArtifactName(kind), nil
}

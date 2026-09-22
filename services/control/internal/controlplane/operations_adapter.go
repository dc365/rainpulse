package controlplane

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operations"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"gopkg.in/yaml.v3"
)

// OperationsBuilder reuses the canonical context selector but never calls a
// Create* command or overwrites the automatic pipeline's current asset pointers.
type OperationsBuilder struct {
	Store *postgresstore.Store
	DB    *sql.DB
}

func NewOperationsBuilder(store *postgresstore.Store, db *sql.DB) *OperationsBuilder {
	return &OperationsBuilder{store, db}
}
func operationsFile(name string) (string, []byte, error) {
	aliases := map[string][]string{
		"RAINPULSE_RADAR_QC_CONFIG":     {"RAINPULSE_OPS_QC_CONFIG", "RAINPULSE_PIPELINE_QC_CONFIG"},
		"RAINPULSE_QC_FLAG_DEFINITIONS": {"RAINPULSE_OPS_FLAG_DEFINITIONS", "RAINPULSE_QC_FLAG_DEFINITIONS"},
		"RAINPULSE_DIAGNOSTIC_CONFIG":   {"RAINPULSE_OPS_DIAGNOSTIC_CONFIG", "RAINPULSE_PIPELINE_DIAGNOSTIC_CONFIG"},
	}
	for _, key := range aliases[name] {
		if value := strings.TrimSpace(os.Getenv(key)); value != "" {
			data, err := os.ReadFile(value)
			if err != nil {
				return "", nil, operations.Invalid("管理预检配置不可读：" + key)
			}
			return value, data, nil
		}
	}
	return "", nil, operations.Invalid("未设置管理预检配置路径：" + name)
}
func (b *OperationsBuilder) ValidateConfiguration(ctx context.Context, spec operations.Spec) error {
	keys := []string{"RAINPULSE_QC_FLAG_DEFINITIONS"}
	if spec.Kind == "qc" {
		keys = append(keys, "RAINPULSE_RADAR_QC_CONFIG")
	}
	if spec.Kind == "diagnostics" {
		keys = append(keys, "RAINPULSE_DIAGNOSTIC_CONFIG")
	}
	for _, key := range keys {
		_, data, err := operationsFile(key)
		if err != nil {
			return err
		}
		if operations.Digest(data) != spec.Identity.Files[key] {
			return fmt.Errorf("%s摘要不同", key)
		}
	}
	return ctx.Err()
}
func (b *OperationsBuilder) Build(ctx context.Context, s operations.Selection) ([]operations.Spec, []operations.Check, error) {
	specs := []operations.Spec{}
	checks := []operations.Check{}
	_, flags, err := operationsFile("RAINPULSE_QC_FLAG_DEFINITIONS")
	if err != nil {
		return specs, checks, err
	}
	var flagConfig struct {
		Version string `yaml:"definition_version"`
	}
	if err = yaml.Unmarshal(flags, &flagConfig); err != nil || flagConfig.Version == "" {
		return nil, nil, operations.Invalid("标志表配置缺少版本")
	}
	flagFiles := map[string]string{"RAINPULSE_QC_FLAG_DEFINITIONS": operations.Digest(flags)}
	if s.Preset == "diagnostics" {
		_, configBytes, e := operationsFile("RAINPULSE_DIAGNOSTIC_CONFIG")
		if e != nil {
			return nil, nil, e
		}
		var config map[string]any
		if e = yaml.Unmarshal(configBytes, &config); e != nil {
			return nil, nil, e
		}
		for _, sourceID := range s.SourceJobIDs {
			job, e := b.Store.GetJob(ctx, uuid.MustParse(sourceID))
			if e != nil {
				return nil, nil, e
			}
			if job.JobType != orchestration.AnalysisDiagnosticsJobType {
				return nil, nil, operations.Invalid("所选任务不是区域诊断任务")
			}
			var registered string
			if e = b.DB.QueryRowContext(ctx, `SELECT sha256 FROM config_versions WHERE config_version=$1`, job.ConfigVersion).Scan(&registered); e != nil {
				return nil, nil, e
			}
			if registered != operations.Digest(configBytes) {
				checks = append(checks, operations.Check{Code: "configuration", State: "BLOCK", Message: "当前诊断配置与源任务注册摘要不同；本预设不暗中升级配置", Target: sourceID})
			}
			var request map[string]any
			if e = json.Unmarshal(job.RequestPayload, &request); e != nil {
				return nil, nil, e
			}
			payload, ok := request["payload"].(map[string]any)
			if !ok {
				return nil, nil, operations.Invalid("源任务没有冻结请求")
			}
			versions := map[string]string{}
			for key, configKey := range map[string]string{"diagnostic_config_version": "profile_version", "renderer_version": "renderer_version", "flag_definition_version": "flag_definition_version"} {
				v, _ := payload[key].(string)
				versions[key] = v
				if actual, _ := config[configKey].(string); v == "" || v != actual {
					checks = append(checks, operations.Check{Code: "version", State: "BLOCK", Message: "诊断参数版本与源任务不同", Target: sourceID})
				}
			}
			spec := operations.Spec{ID: operations.NewID(), Kind: "diagnostics", Name: "区域诊断重建 · " + sourceID[:8], SourceJobID: sourceID, InputURIs: operationsInputURIs(payload), Request: append([]byte(nil), job.RequestPayload...), Identity: operations.Identity{Kind: "diagnostics", Versions: versions, Files: map[string]string{"RAINPULSE_QC_FLAG_DEFINITIONS": operations.Digest(flags), "RAINPULSE_DIAGNOSTIC_CONFIG": operations.Digest(configBytes)}}}
			specs = append(specs, spec)
		}
		return specs, checks, nil
	}
	var qc qcConfiguration
	var qcBytes []byte
	if s.Preset == "qc_preview" {
		_, qcBytes, err = operationsFile("RAINPULSE_RADAR_QC_CONFIG")
		if err != nil {
			return nil, nil, err
		}
		if err = yaml.Unmarshal(qcBytes, &qc); err != nil {
			return nil, nil, err
		}
		if qc.ProfileVersion == "" || qc.PipelineVersion == "" || qc.FlagDefinitionVersion != flagConfig.Version {
			return nil, nil, operations.Invalid("QC版本或标志表配置不一致")
		}
	}
	snapshot := time.Now().UTC()
	total := 0
	seen := map[uuid.UUID]bool{}
	for _, radar := range s.RadarIDs {
		radar = strings.ToLower(radar)
		query := workflow.RadarScanListQuery{Limit: 64, RadarID: &radar, StartTime: &s.Start, EndTime: &s.End, SnapshotTime: snapshot}
		radarCount := 0
		for {
			page, e := b.Store.ListRadarScansByQuery(ctx, query)
			if e != nil {
				return nil, nil, e
			}
			for _, scan := range page.Items {
				if seen[scan.ID] {
					continue
				}
				seen[scan.ID] = true
				radarCount++
				total++
				if total > 64 {
					return nil, nil, operations.Invalid("首版单次最多64个体扫，请缩小时间范围；未静默截断")
				}
				name := strings.ToUpper(scan.RadarID) + " · " + scan.VolumeEndTime.UTC().Format("01-02 15:04 UTC")
				preview := operations.Spec{ID: operations.NewID(), Kind: "render", Name: name + " · 对照图", InputURIs: []string{}, Identity: operations.Identity{Kind: "render", Files: flagFiles, Versions: map[string]string{"flag_definition_version": flagConfig.Version, "preview_version": "ops-preview-1"}}}
				previewRequest := map[string]any{"schema_version": "1.0", "event_type": "ops.preview.requested.v1", "event_id": preview.ID, "job_id": preview.ID, "run_id": preview.ID, "trace_id": preview.ID, "occurred_at": snapshot, "payload": map[string]any{"scan_id": scan.ID.String(), "radar_id": scan.RadarID, "input_uri": "", "output_prefix": "s3://rainpulse/operations/planned/", "flag_definition_version": flagConfig.Version}}
				if s.Preset == "render_only" {
					if scan.QCURI == nil || *scan.QCURI == "" {
						checks = append(checks, operations.Check{Code: "qc_input", State: "BLOCK", Message: "体扫没有已登记QC资产；不能仅重建图件", Target: scan.ID.String()})
						continue
					}
					preview.InputURIs = []string{*scan.QCURI}
					previewRequest["payload"].(map[string]any)["input_uri"] = *scan.QCURI
					preview.Request = operations.JSON(previewRequest)
					specs = append(specs, preview)
					continue
				}
				if scan.NormalizedURI == nil || *scan.NormalizedURI == "" {
					checks = append(checks, operations.Check{Code: "normalized_input", State: "BLOCK", Message: "原始目录记录存在，但标准化体扫不可用；请先完成解码", Target: scan.ID.String()})
					continue
				}
				health, e := b.Store.GetRadarHealthMetrics(ctx, scan.ID)
				if e != nil || health.Health == workflow.RadarHealthUnavailable || health.Health == workflow.RadarHealthUnknown {
					checks = append(checks, operations.Check{Code: "radar_health", State: "BLOCK", Message: "体扫健康记录未知或不可用", Target: scan.ID.String()})
					continue
				}
				cf := qc.RadialInterference.Morphology.ContextFusion
				if qc.Engine == "open_source" {
					cf = qcContextFusionConfiguration{Enabled: qc.Context.Enabled, MaximumTemporalContextScans: qc.Context.MaxTemporalScans, TemporalMaxTimeOffsetSecs: qc.Context.MaxAgeSeconds, TemporalSelectionMode: "past_only", CrossRadarMaximumTimeOffsetSecs: qc.Context.MaxCrossOffsetSeconds}
				}
				temporal, cross, e := selectRadarQCContext(ctx, b.Store, scan, cf)
				if e != nil {
					return nil, nil, e
				}
				if cf.Enabled && len(temporal) == 0 {
					checks = append(checks, operations.Check{Code: "temporal_context", State: "WARN", Message: "没有可选历史上下文；沿用算法的缺证据语义，不假造观测", Target: scan.ID.String()})
				}
				id := operations.NewID()
				hash := ""
				if strings.HasPrefix(qc.PipelineVersion, "qc-opensource-") {
					hash = operations.Digest(qcBytes)
				}
				request := orchestration.RadarQCRequested{SchemaVersion: "1.0", EventID: uuid.MustParse(id), EventType: orchestration.RadarQCRequestedEventType, OccurredAt: snapshot, RunID: uuid.MustParse(id), JobID: uuid.MustParse(id), TraceID: uuid.MustParse(id), Payload: orchestration.RadarQCRequestedPayload{ScanID: scan.ID, RadarID: scan.RadarID, InputURI: *scan.NormalizedURI, OutputPrefix: "s3://rainpulse/operations/planned/", RadarConfig: scan.RadarConfigVersion, QCProfile: qc.ProfileVersion, QCPipelineVersion: qc.PipelineVersion, QCProfileSHA256: hash, FlagDefinitionVersion: qc.FlagDefinitionVersion, TemporalContext: temporal, CrossRadarContext: cross}}
				uris := []string{*scan.NormalizedURI}
				for _, v := range temporal {
					uris = append(uris, v.InputURI)
				}
				for _, v := range cross {
					uris = append(uris, v.InputURI)
				}
				spec := operations.Spec{ID: id, Kind: "qc", Name: name + " · 单站QC", InputURIs: uris, Request: operations.JSON(request), Identity: operations.Identity{Kind: "qc", Files: map[string]string{"RAINPULSE_RADAR_QC_CONFIG": operations.Digest(qcBytes), "RAINPULSE_QC_FLAG_DEFINITIONS": operations.Digest(flags)}, Versions: map[string]string{"qc_profile": qc.ProfileVersion, "qc_pipeline_version": qc.PipelineVersion, "flag_definition_version": flagConfig.Version}}}
				preview.ParentID = id
				preview.Request = operations.JSON(previewRequest)
				specs = append(specs, spec, preview)
			}
			if page.NextCursor == nil {
				break
			}
			query.Cursor = page.NextCursor
		}
		if radarCount == 0 {
			checks = append(checks, operations.Check{Code: "radar_missing", State: "BLOCK", Message: "所选站点在该范围没有数据；请补资料或调整选择", Target: radar})
		}
	}
	checks = append(checks, operations.Check{Code: "inventory", State: "PASS", Message: fmt.Sprintf("目录选中%d个实际体扫；不以六分钟产品间隔推断原始雷达应到频次", total)})
	return specs, checks, nil
}
func operationsInputURIs(v any) []string {
	out := []string{}
	seen := map[string]bool{}
	var visit func(any)
	visit = func(v any) {
		switch x := v.(type) {
		case map[string]any:
			for k, item := range x {
				if k == "input_uri" || k == "qc_uri" || k == "grid_uri" || k == "analysis_uri" {
					if uri, ok := item.(string); ok && strings.HasPrefix(uri, "s3://") && !seen[uri] {
						seen[uri] = true
						out = append(out, uri)
					}
				}
				visit(item)
			}
		case []any:
			for _, item := range x {
				visit(item)
			}
		}
	}
	visit(v)
	return out
}

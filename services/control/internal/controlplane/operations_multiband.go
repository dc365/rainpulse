package controlplane

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/multiband"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operations"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
)

func managedNetwork() (multiband.Network, error) {
	path := strings.TrimSpace(os.Getenv("RAINPULSE_MULTIBAND_CONFIG"))
	if path == "" {
		return multiband.Network{}, operations.Invalid("尚未配置经过核对的S/X网络文件 RAINPULSE_MULTIBAND_CONFIG")
	}
	return multiband.Load(path)
}

const defaultExecutionPolicySHA256 = "dfbf5acd53e2ef84a7c44b77f6a0ed2969d7cdfa8942d48c8bdb10e444063725"

func managedExecutionPolicy() (string, string, error) {
	path := strings.TrimSpace(os.Getenv("RAINPULSE_MULTIBAND_EXECUTION_CONFIG"))
	if path == "" {
		return "", defaultExecutionPolicySHA256, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) > 64*1024 || !json.Valid(raw) {
		return "", "", operations.Invalid("多波段执行策略不可读或无效：RAINPULSE_MULTIBAND_EXECUTION_CONFIG")
	}
	return path, operations.Digest(raw), nil
}

func (b *OperationsBuilder) validateMultiBand(ctx context.Context, spec operations.Spec) error {
	n, e := managedNetwork()
	if e != nil {
		return e
	}
	if spec.Identity.Files["RAINPULSE_MULTIBAND_CONFIG"] != n.SHA256 {
		return operations.Conflict("S/X网络发布内容已改变")
	}
	_, flags, e := operationsFile("RAINPULSE_QC_FLAG_DEFINITIONS")
	if e != nil {
		return e
	}
	if spec.Identity.Files["RAINPULSE_QC_FLAG_DEFINITIONS"] != operations.Digest(flags) {
		return operations.Conflict("S兼容适配器标志表已改变")
	}
	path, policySHA, e := managedExecutionPolicy()
	if e != nil {
		return e
	}
	if spec.Identity.Versions["execution_policy_sha256"] != policySHA {
		return operations.Conflict("多波段执行策略已改变")
	}
	if path != "" && spec.Identity.Files["RAINPULSE_MULTIBAND_EXECUTION_CONFIG"] != policySHA {
		return operations.Conflict("多波段执行策略摘要已改变")
	}
	return ctx.Err()
}
func (b *OperationsBuilder) buildMultiBand(ctx context.Context, s operations.Selection) ([]operations.Spec, []operations.Check, error) {
	n, e := managedNetwork()
	if e != nil {
		return nil, nil, e
	}
	// This migration adds only an existing operations kind/pool, no second task DB.
	var version int
	if e = b.DB.QueryRowContext(ctx, "SELECT version FROM ops_schema WHERE version=4").Scan(&version); e != nil {
		return nil, nil, operations.Invalid("请先执行 schema_multiband_v1.sql")
	}
	product := s.ProductID
	if s.Preset == "sx_composite" && product == "" && len(n.Products) == 1 {
		for key := range n.Products {
			product = key
		}
	}
	if s.Preset == "sx_composite" {
		if _, ok := n.Products[product]; !ok {
			return nil, nil, operations.Invalid("请选择网络配置中已有的 product_id")
		}
	} else if product != "" {
		return nil, nil, operations.Invalid("请选择网络配置中已有的 product_id")
	}
	_, flagBytes, e := operationsFile("RAINPULSE_QC_FLAG_DEFINITIONS")
	if e != nil {
		return nil, nil, e
	}
	executionPath, executionSHA, e := managedExecutionPolicy()
	if e != nil {
		return nil, nil, e
	}
	cutoff := time.Now().UTC()
	scans := []multiband.Scan{}
	for _, rawID := range s.RadarIDs {
		id := strings.ToLower(rawID)
		station, ok := n.Stations[id]
		available := station.Enabled
		if s.Preset == "x_qc" {
			available = station.Enabled || station.XQCEnabled
		}
		if !ok || !available || s.Preset == "x_qc" && station.Band != "X" {
			return nil, nil, operations.Invalid("未启用或未登记的S/X站点：" + id)
		}
		earliest := s.Start.Add(-time.Duration(station.MaximumAge) * time.Second)
		// End is exclusive for X standalone; allow exact current product target for CR.
		end := s.End
		query := workflow.RadarScanListQuery{Limit: 200, RadarID: &id, StartTime: &earliest, EndTime: &end, SnapshotTime: cutoff}
		for {
			page, err := b.Store.ListRadarScansByQuery(ctx, query)
			if err != nil {
				return nil, nil, err
			}
			for _, scan := range page.Items {
				item := multiband.Scan{ID: scan.ID.String(), RadarID: scan.RadarID, Start: scan.VolumeStartTime, End: scan.VolumeEndTime, AvailableAt: scan.CreatedAt}
				if scan.NormalizedURI != nil {
					item.NormalizedURI = *scan.NormalizedURI
				}
				if scan.QCURI != nil {
					item.QCURI = *scan.QCURI
				}
				// QC may have completed after raw ingest. Freeze the later catalog update,
				// rather than pretend a newly derived asset existed at original ingest.
				if station.Band == "S" && scan.UpdatedAt.After(item.AvailableAt) {
					item.AvailableAt = scan.UpdatedAt
				}
				scans = append(scans, item)
				if len(scans) > 4096 {
					return nil, nil, operations.Invalid("多波段目录超过4096条，请缩小时间范围")
				}
			}
			if page.NextCursor == nil {
				break
			}
			query.Cursor = page.NextCursor
		}
	}
	ids := append([]string(nil), s.RadarIDs...)
	for i := range ids {
		ids[i] = strings.ToLower(ids[i])
	}
	sort.Strings(ids)
	snapshots, warnings, e := multiband.Select(n, product, s.Preset, ids, s.Start, s.End, cutoff, scans)
	if e != nil {
		return nil, nil, operations.Invalid(e.Error())
	}
	checks := []operations.Check{{Code: "multiband_scope", State: "WARN", Message: "S复用已登记QC；X单站任务只运行原生极坐标候选质控，不要求融合几何；区域组合仍须完整核验几何与标定。仅候选，不改变QPE/预报/默认展示。"},
		{Code: "cutoff", State: "WARN", Message: "输入按本次预检可见目录冻结；历史计划不等于当时实时到报可得性回放。"}}
	for _, warning := range warnings {
		checks = append(checks, operations.Check{Code: "station_availability", State: "WARN", Message: warning})
	}
	specs := []operations.Spec{}
	for _, snap := range snapshots {
		id := operations.NewID()
		payload := map[string]any{"mode": s.Preset, "network_sha256": n.SHA256, "analysis_time": snap.AnalysisTime,
			"input_cutoff": cutoff, "sources": snap.Sources, "output_prefix": "s3://rainpulse/operations/planned/", "analysis_id": snap.Slot,
			"execution_sha256": executionSHA}
		if s.Preset == "sx_composite" {
			payload["product_id"] = product
		}
		if s.Preset == "x_qc" {
			payload["scan_id"] = snap.Sources[0].ScanID
			payload["radar_id"] = snap.Sources[0].RadarID
		}
		request := map[string]any{"schema_version": "1.0", "event_type": "ops.multiband.requested.v1", "event_id": id, "job_id": id, "run_id": id, "trace_id": id, "occurred_at": cutoff, "payload": payload}
		uris := []string{}
		for _, src := range snap.Sources {
			uris = append(uris, src.InputURI)
		}
		taskName := fmt.Sprintf("%s · %s · %s", s.Preset, product, snap.AnalysisTime.Format("01-02 15:04 UTC"))
		if s.Preset == "x_qc" {
			taskName = fmt.Sprintf("X QC · %s · %s", snap.Sources[0].RadarID, snap.Sources[0].ScanID)
		}
		files := map[string]string{"RAINPULSE_MULTIBAND_CONFIG": n.SHA256, "RAINPULSE_QC_FLAG_DEFINITIONS": operations.Digest(flagBytes)}
		if executionPath != "" {
			files["RAINPULSE_MULTIBAND_EXECUTION_CONFIG"] = executionSHA
		}
		specs = append(specs, operations.Spec{ID: id, Kind: "multiband", Name: taskName, InputURIs: uris, Request: operations.JSON(request),
			Identity: operations.Identity{Kind: "multiband", Files: files, Versions: map[string]string{"multiband_contract": "rainpulse.multiband.v1", "network_release": n.Release, "execution_policy_sha256": executionSHA}}})
	}
	state := "PASS"
	if len(specs) == 0 {
		state = "BLOCK"
	}
	checks = append(checks, operations.Check{Code: "multiband_tasks", State: state, Message: fmt.Sprintf("冻结%d个六分钟产品或X单站任务；不对S重复运行QC。", len(specs))})
	return specs, checks, nil
}

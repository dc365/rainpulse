package workspace

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
)

// dataflowStageLabel renders the domain stage identity for humans. It stays in
// sync with the job-type mapping done by the store layer.
var dataflowStageLabel = map[string]string{
	"decode":        "解码",
	"qc":            "质控",
	"grid":          "格点化",
	"mosaic":        "拼图",
	"qpe":           "QPE",
	"diagnostics":   "诊断产品",
	"nowcast_input": "模型输入",
	"pysteps_lk":    "pySTEPS-LK",
	"pysteps_steps": "pySTEPS-STEPS",
	"nowcastnet":    "NowcastNet",
	"products":      "应用产品",
	"verification":  "检验",
}

const dataflowEventCap = 30

// dataflowTimedTolerance separates live processing from backfilled re-runs:
// job/receipt timestamps within this distance of the scan's data time count as
// live timing; anything further (history ingested or reprocessed weeks later)
// falls back to the data time so the ticker stays on the wall-clock axis.
const dataflowTimedTolerance = 30 * time.Minute

func dataflowEffectiveTime(real *time.Time, dataTime time.Time) time.Time {
	if real == nil || absTimeDuration(real.Sub(dataTime)) > dataflowTimedTolerance {
		return dataTime
	}
	return *real
}

// AssembleDataflowSnapshot is the pure projection step from collected window
// rows to the UI payload: lane grouping, chain beat strip aggregation, per-radar
// QC duration and the derived event ticker. The anchor is the wall-clock right
// edge of the window (live follow passes now; historical replay passes the
// requested moment). It never touches the database, so the shape is
// unit-testable without PostgreSQL.
func AssembleDataflowSnapshot(
	anchor time.Time,
	generatedAt time.Time,
	window time.Duration,
	scans []DataflowScanBlock,
	analyses []DataflowAnalysisBlock,
	forecasts []DataflowForecastBlock,
	statuses []workflow.RadarStatusSummary,
	warnings []string,
) DataflowSnapshot {
	snapshot := DataflowSnapshot{
		SchemaVersion:  "1.0",
		GeneratedAt:    generatedAt.UTC(),
		WindowStart:    anchor.UTC().Add(-window),
		WindowMinutes:  int(window / time.Minute),
		Stages:         dataflowEmptyStrip(),
		RadarLanes:     dataflowRadarLanes(scans, statuses),
		AnalysisBlocks: analyses,
		ForecastBlocks: forecasts,
		RadarStatuses:  dataflowRadarStatuses(statuses, scans),
		Events:         dataflowEvents(anchor, window, scans, analyses, forecasts),
		Warnings:       warnings,
	}
	dataflowCountStrip(&snapshot, scans)
	return snapshot
}

func dataflowEmptyStrip() []DataflowStageSummary {
	result := make([]DataflowStageSummary, 0, len(DataflowStripOrder))
	for _, node := range DataflowStripOrder {
		result = append(result, DataflowStageSummary{Key: node.Key, Label: node.Label})
	}
	return result
}

// dataflowRadarLanes groups scans per radar. A radar from the status roster
// keeps its lane even with no scan inside the window — silence is evidence —
// except never-registered/unavailable candidates, which would only add noise.
func dataflowRadarLanes(scans []DataflowScanBlock, statuses []workflow.RadarStatusSummary) []DataflowRadarLane {
	byRadar := make(map[string][]DataflowScanBlock)
	order := make([]string, 0)
	for _, scan := range scans {
		if _, seen := byRadar[scan.RadarID]; !seen {
			order = append(order, scan.RadarID)
		}
		byRadar[scan.RadarID] = append(byRadar[scan.RadarID], scan)
	}
	for _, status := range statuses {
		if _, seen := byRadar[status.RadarID]; seen {
			continue
		}
		if status.Health == workflow.RadarHealthUnavailable {
			continue
		}
		order = append(order, status.RadarID)
		byRadar[status.RadarID] = []DataflowScanBlock{}
	}
	sort.Strings(order)
	lanes := make([]DataflowRadarLane, 0, len(order))
	for _, radarID := range order {
		blocks := byRadar[radarID]
		sort.SliceStable(blocks, func(left, right int) bool {
			return blocks[left].VolumeStart.Before(blocks[right].VolumeStart)
		})
		for index := range blocks {
			blocks[index].Stages = dataflowSortedStages(blocks[index].Stages)
		}
		lanes = append(lanes, DataflowRadarLane{RadarID: radarID, Blocks: blocks})
	}
	return lanes
}

func dataflowSortedStages(stages []DataflowJobStage) []DataflowJobStage {
	sort.SliceStable(stages, func(left, right int) bool {
		return dataflowStageRank(stages[left].Stage) < dataflowStageRank(stages[right].Stage)
	})
	return stages
}

func dataflowStageRank(stage string) int {
	order := map[string]int{
		"decode": 10, "qc": 20, "grid": 30, "mosaic": 40, "qpe": 50,
		"diagnostics": 55, "nowcast_input": 60, "pysteps_lk": 70,
		"pysteps_steps": 71, "nowcastnet": 72, "products": 80, "verification": 90,
	}
	if value, ok := order[stage]; ok {
		return value
	}
	return 100
}

// dataflowCountStrip fills the chain beat nodes. The ingest node counts volume
// scan arrivals; every other node aggregates its mapped jobs across the radar,
// analysis and forecast lanes.
func dataflowCountStrip(snapshot *DataflowSnapshot, scans []DataflowScanBlock) {
	type group struct {
		completed int
		running   int
		queued    int
		failed    int
		runtimes  []int64
	}
	groups := make(map[string]*group)
	ensure := func(key string) *group {
		if groups[key] == nil {
			groups[key] = &group{}
		}
		return groups[key]
	}
	for index := range snapshot.Stages {
		if snapshot.Stages[index].Key == "ingest" {
			for _, scan := range scans {
				snapshot.Stages[index].Completed++
				switch {
				case scan.Status == "FAILED":
					snapshot.Stages[index].Failed++
				case !dataflowScanTerminal(scan.Status):
					snapshot.Stages[index].Running++
				}
			}
		}
	}
	countJob := func(stage DataflowJobStage) {
		key := dataflowStripGroup(stage.Stage)
		if key == "" {
			return
		}
		item := ensure(key)
		switch stage.Status {
		case "SUCCEEDED":
			item.completed++
			if stage.FinishedAt != nil && stage.StartedAt != nil {
				runtime := stage.FinishedAt.Sub(*stage.StartedAt).Milliseconds()
				if runtime > 0 {
					item.runtimes = append(item.runtimes, runtime)
				}
			}
		case "RUNNING":
			item.running++
		case "PENDING":
			item.queued++
		case "FAILED":
			item.failed++
		}
	}
	for _, scan := range scans {
		for _, stage := range scan.Stages {
			countJob(stage)
		}
	}
	for _, block := range snapshot.AnalysisBlocks {
		for _, stage := range block.Stages {
			countJob(stage)
		}
	}
	for _, block := range snapshot.ForecastBlocks {
		for _, stage := range block.Stages {
			countJob(stage)
		}
	}
	for index := range snapshot.Stages {
		item := groups[snapshot.Stages[index].Key]
		if item == nil {
			continue
		}
		snapshot.Stages[index].Completed = item.completed
		snapshot.Stages[index].Running = item.running
		snapshot.Stages[index].Queued = item.queued
		snapshot.Stages[index].Failed = item.failed
		snapshot.Stages[index].P50MS = dataflowMedian(item.runtimes)
	}
}

func dataflowScanTerminal(status string) bool {
	switch status {
	case "RADAR_GRID_READY", "DEGRADED", "FAILED", "SKIPPED":
		return true
	}
	return false
}

func dataflowMedian(values []int64) int64 {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]int64(nil), values...)
	sort.Slice(sorted, func(left, right int) bool { return sorted[left] < sorted[right] })
	middle := len(sorted) / 2
	if len(sorted)%2 == 1 {
		return sorted[middle]
	}
	return (sorted[middle-1] + sorted[middle]) / 2
}

func dataflowRadarStatuses(statuses []workflow.RadarStatusSummary, scans []DataflowScanBlock) []DataflowRadarStatus {
	type latestQCJob struct {
		finished time.Time
		runtime  *int64
	}
	latestQC := make(map[string]latestQCJob)
	for _, scan := range scans {
		for _, stage := range scan.Stages {
			if stage.Stage != "qc" || stage.Status != "SUCCEEDED" || stage.FinishedAt == nil {
				continue
			}
			runtime := dataflowStageRuntimeMS(stage)
			existing, seen := latestQC[scan.RadarID]
			if seen && !stage.FinishedAt.After(existing.finished) {
				continue
			}
			latestQC[scan.RadarID] = latestQCJob{finished: *stage.FinishedAt, runtime: runtime}
		}
	}
	result := make([]DataflowRadarStatus, 0, len(statuses))
	for _, status := range statuses {
		item := DataflowRadarStatus{
			RadarID:                       status.RadarID,
			Health:                        string(status.Health),
			LatestScanTime:                status.LatestScanTime,
			ScanCompleteness:              status.ScanCompleteness,
			MeanQualityIndex:              status.MeanQualityIndex,
			DataDelaySeconds:              status.DataDelaySeconds,
			ParticipatingInLatestAnalysis: status.ParticipatingInLatestAnalysis,
		}
		if latest, ok := latestQC[status.RadarID]; ok {
			item.QCDurationMS = latest.runtime
		}
		if status.DisplayName != nil {
			item.DisplayName = *status.DisplayName
		}
		if status.ScanStatus != nil {
			item.ScanStatus = string(*status.ScanStatus)
		}
		result = append(result, item)
	}
	return result
}

func dataflowStageRuntimeMS(stage DataflowJobStage) *int64 {
	if stage.RuntimeMS != nil {
		return stage.RuntimeMS
	}
	if stage.FinishedAt == nil || stage.StartedAt == nil {
		return nil
	}
	value := stage.FinishedAt.Sub(*stage.StartedAt).Milliseconds()
	if value < 0 {
		return nil
	}
	return &value
}

// dataflowClockZone is the operator-facing CST clock used inside event labels.
// UTC remains authoritative in every machine-readable timestamp field.
var dataflowClockZone = time.FixedZone("CST", 8*60*60)

func dataflowEventDetail(stage DataflowJobStage) string {
	runtime := dataflowStageRuntimeMS(stage)
	if runtime == nil {
		return ""
	}
	return dataflowFormatDuration(*runtime)
}

func dataflowFormatDuration(ms int64) string {
	if ms < 1000 {
		return fmt.Sprintf("%d ms", ms)
	}
	return fmt.Sprintf("%.1f s", float64(ms)/1000)
}

// dataflowEvents derives the ticker entries from the same window rows. It only
// reports moments that already happened relative to the anchor: arrivals,
// finished jobs, created automatic analysis cycles and published automatic
// forecast runs.
func dataflowEvents(
	anchor time.Time,
	window time.Duration,
	scans []DataflowScanBlock,
	analyses []DataflowAnalysisBlock,
	forecasts []DataflowForecastBlock,
) []DataflowEvent {
	events := make([]DataflowEvent, 0, dataflowEventCap*2)
	windowStart := anchor.UTC().Add(-window)
	for _, scan := range scans {
		events = append(events, DataflowEvent{
			Time: dataflowEffectiveTime(&scan.ReceivedAt, scan.VolumeEnd), Kind: "scan.received", RadarID: scan.RadarID,
			Label: fmt.Sprintf("%s 数据到达", strings.ToUpper(scan.RadarID)),
		})
		for _, stage := range scan.Stages {
			if stage.FinishedAt == nil {
				continue
			}
			label := fmt.Sprintf("%s · %s", dataflowStageDisplay(stage.Stage), strings.ToUpper(scan.RadarID))
			when := dataflowEffectiveTime(stage.FinishedAt, scan.VolumeEnd)
			switch stage.Status {
			case "SUCCEEDED":
				events = append(events, DataflowEvent{
					Time: when, Kind: "job.succeeded", RadarID: scan.RadarID,
					Label: label, Detail: dataflowEventDetail(stage),
				})
			case "FAILED":
				events = append(events, DataflowEvent{
					Time: when, Kind: "job.failed", RadarID: scan.RadarID,
					Label: label, Detail: stage.ErrorCode,
				})
			}
		}
	}
	for _, block := range analyses {
		events = append(events, DataflowEvent{
			Time: dataflowEffectiveTime(&block.CreatedAt, block.AnalysisTime), Kind: "analysis.created",
			Label: fmt.Sprintf("分析周期 %s · %d 部雷达", block.AnalysisTime.In(dataflowClockZone).Format("15:04"), block.RadarCount),
		})
	}
	for _, block := range forecasts {
		when := dataflowEffectiveTime(block.UpdatedAt, block.IssueTime)
		if block.UpdatedAt != nil && block.UpdatedAt.Before(windowStart) {
			continue
		}
		switch block.Status {
		case "PUBLISHED", "VERIFYING", "VERIFIED":
			events = append(events, DataflowEvent{
				Time: when, Kind: "run.published",
				Label: fmt.Sprintf("预报发布 %s", block.IssueTime.In(dataflowClockZone).Format("15:04")),
			})
		case "FAILED":
			events = append(events, DataflowEvent{
				Time: when, Kind: "run.published",
				Label: fmt.Sprintf("预报失败 %s", block.IssueTime.In(dataflowClockZone).Format("15:04")),
			})
		}
	}
	sort.SliceStable(events, func(left, right int) bool { return events[left].Time.After(events[right].Time) })
	if len(events) > dataflowEventCap {
		events = events[:dataflowEventCap]
	}
	return events
}

func dataflowStageDisplay(stage string) string {
	if label, ok := dataflowStageLabel[stage]; ok {
		return label
	}
	return stage
}

// BuildDataflowStageTrend buckets succeeded-job runtimes per chain group and
// takes the median of each bucket. The ingest node has no runtimes and is
// left out of the series.
func BuildDataflowStageTrend(
	generatedAt time.Time,
	hours int,
	bucketMinutes int,
	samples []DataflowStageRuntimeSample,
) DataflowStageTrend {
	bucketCount := hours * 60 / bucketMinutes
	buckets := make([]time.Time, bucketCount)
	for index := range buckets {
		buckets[index] = generatedAt.UTC().Add(-time.Duration(bucketCount-1-index) * time.Duration(bucketMinutes) * time.Minute).Truncate(time.Duration(bucketMinutes) * time.Minute)
	}
	perGroup := make(map[string][][]int64, len(DataflowStripOrder))
	for _, sample := range samples {
		offset := int(generatedAt.UTC().Sub(sample.FinishedAt).Minutes()) / bucketMinutes
		index := bucketCount - 1 - offset
		if index < 0 || index >= bucketCount {
			continue
		}
		if perGroup[sample.Group] == nil {
			perGroup[sample.Group] = make([][]int64, bucketCount)
		}
		perGroup[sample.Group][index] = append(perGroup[sample.Group][index], sample.RuntimeMS)
	}
	series := make([]DataflowStageTrendSeries, 0, len(DataflowStripOrder)-1)
	for _, node := range DataflowStripOrder {
		if node.Key == "ingest" {
			continue
		}
		runtimes := perGroup[node.Key]
		values := make([]*int64, bucketCount)
		if len(runtimes) != bucketCount {
			series = append(series, DataflowStageTrendSeries{Key: node.Key, Label: node.Label, Values: values})
			continue
		}
		for index := range buckets {
			if len(runtimes[index]) == 0 {
				continue
			}
			median := dataflowMedian(runtimes[index])
			value := median
			values[index] = &value
		}
		series = append(series, DataflowStageTrendSeries{Key: node.Key, Label: node.Label, Values: values})
	}
	return DataflowStageTrend{
		SchemaVersion: "1.0",
		GeneratedAt:   generatedAt.UTC(),
		Hours:         hours,
		BucketMinutes: bucketMinutes,
		Buckets:       buckets,
		Series:        series,
	}
}

package workspace

import (
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
)

func dataflowTestClock() time.Time {
	return time.Date(2026, 10, 8, 4, 30, 0, 0, time.UTC)
}

func dataflowStageFixture(stage, status string, created, started, finished *time.Time, runtime *int64) DataflowJobStage {
	value := DataflowJobStage{Stage: stage, JobID: stage + "-" + status, Status: status, CreatedAt: dataflowTestClock().Add(-8 * time.Minute)}
	if created != nil {
		value.CreatedAt = *created
	}
	value.StartedAt, value.FinishedAt, value.RuntimeMS = started, finished, runtime
	return value
}

func dataflowScanFixture(radarID, status string, volumeStart time.Time, stages ...DataflowJobStage) DataflowScanBlock {
	return DataflowScanBlock{
		ScanID: "scan-" + radarID + "-" + volumeStart.Format("150405"), RunID: "run-" + radarID + "-" + volumeStart.Format("150405"),
		RadarID: radarID, VolumeStart: volumeStart, VolumeEnd: volumeStart.Add(5 * time.Minute),
		ReceivedAt: volumeStart.Add(5 * time.Minute), Status: status, Stages: stages,
	}
}

func TestAssembleDataflowSnapshotStripCountsAndMedian(t *testing.T) {
	now := dataflowTestClock()
	started := now.Add(-3 * time.Minute)
	decodeDone := dataflowStageFixture("decode", "SUCCEEDED",
		ptrTime(now.Add(-6*time.Minute)), ptrTime(now.Add(-6*time.Minute)), ptrTime(now.Add(-5*time.Minute)), nil)
	qcDone := dataflowStageFixture("qc", "SUCCEEDED",
		nil, ptrTime(now.Add(-5*time.Minute)), ptrTime(now.Add(-4*time.Minute)), nil)
	qcRunning := dataflowStageFixture("qc", "RUNNING",
		nil, ptrTime(started), nil, nil)
	gridQueued := dataflowStageFixture("grid", "PENDING", nil, nil, nil, nil)
	finished := now.Add(-2 * time.Minute)
	decodeDoneLater := dataflowStageFixture("decode", "SUCCEEDED",
		ptrTime(now.Add(-8*time.Minute)), ptrTime(now.Add(-8*time.Minute)), ptrTime(finished), nil)

	scans := []DataflowScanBlock{
		dataflowScanFixture("z9591", "QC_RUNNING", now.Add(-6*time.Minute), decodeDone, qcDone),
		dataflowScanFixture("z9591", "GRID_RUNNING", now.Add(-12*time.Minute), decodeDoneLater, qcRunning, gridQueued),
		dataflowScanFixture("z9593", "RADAR_GRID_READY", now.Add(-6*time.Minute), decodeDone, qcDone),
	}
	snapshot := AssembleDataflowSnapshot(now, now, time.Hour, scans, nil, nil, nil, nil)

	strip := make(map[string]DataflowStageSummary, len(snapshot.Stages))
	for _, item := range snapshot.Stages {
		strip[item.Key] = item
	}
	if got := strip["ingest"]; got.Completed != 3 || got.Running != 2 {
		t.Fatalf("ingest strip = %+v; want 3 arrivals with 2 still running", got)
	}
	if got := strip["decode"]; got.Completed != 3 || got.Failed != 0 {
		t.Fatalf("decode strip = %+v; want 3 completed", got)
	}
	// decode runtimes: 1m, 1m (shared fixture across two scans) and 6m -> median 60000ms
	if got := strip["decode"]; got.P50MS != 60000 {
		t.Fatalf("decode p50 = %d ms; want 60000", got.P50MS)
	}
	if got := strip["qc"]; got.Completed != 2 || got.Running != 1 {
		t.Fatalf("qc strip = %+v; want 2 completed 1 running", got)
	}
	if got := strip["grid"]; got.Queued != 1 {
		t.Fatalf("grid strip = %+v; want 1 queued", got)
	}
	if len(snapshot.Stages) != len(DataflowStripOrder) {
		t.Fatalf("strip length = %d; want %d", len(snapshot.Stages), len(DataflowStripOrder))
	}
}

func TestAssembleDataflowSnapshotLanesSortedAndStagesOrdered(t *testing.T) {
	now := dataflowTestClock()
	later := dataflowScanFixture("z9591", "RADAR_GRID_READY", now.Add(-6*time.Minute))
	earlier := dataflowScanFixture("z9591", "RADAR_GRID_READY", now.Add(-30*time.Minute))
	other := dataflowScanFixture("z9593", "RADAR_GRID_READY", now.Add(-6*time.Minute))
	snapshot := AssembleDataflowSnapshot(now, now, time.Hour, []DataflowScanBlock{later, earlier, other}, nil, nil, nil, nil)
	if len(snapshot.RadarLanes) != 2 {
		t.Fatalf("lane count = %d; want 2", len(snapshot.RadarLanes))
	}
	if snapshot.RadarLanes[0].RadarID != "z9591" || snapshot.RadarLanes[1].RadarID != "z9593" {
		t.Fatalf("lane order = %s, %s; want sorted radar ids", snapshot.RadarLanes[0].RadarID, snapshot.RadarLanes[1].RadarID)
	}
	z9591 := snapshot.RadarLanes[0].Blocks
	if len(z9591) != 2 || z9591[0].VolumeStart.After(z9591[1].VolumeStart) {
		t.Fatalf("z9591 blocks not ordered by volume start: %+v", z9591)
	}
}

func TestAssembleDataflowSnapshotKeepsSilentRadarLanes(t *testing.T) {
	now := dataflowTestClock()
	withData := dataflowScanFixture("z9591", "RADAR_GRID_READY", now.Add(-6*time.Minute))
	statuses := []workflow.RadarStatusSummary{
		{RadarID: "z9593", Health: workflow.RadarHealthDegraded},
		{RadarID: "z9595", Health: workflow.RadarHealthUnavailable},
	}
	snapshot := AssembleDataflowSnapshot(now, now, time.Hour, []DataflowScanBlock{withData}, nil, nil, statuses, nil)
	if len(snapshot.RadarLanes) != 2 {
		t.Fatalf("lane count = %d; want z9591 plus the silent z9593", len(snapshot.RadarLanes))
	}
	silent := snapshot.RadarLanes[1]
	if silent.RadarID != "z9593" || len(silent.Blocks) != 0 {
		t.Fatalf("silent lane = %+v; want z9593 with no blocks", silent)
	}
}

func TestAssembleDataflowSnapshotEventsNewestFirstAndCapped(t *testing.T) {
	now := dataflowTestClock()
	finished := now.Add(-2 * time.Minute)
	failed := dataflowStageFixture("qc", "FAILED",
		nil, ptrTime(now.Add(-4*time.Minute)), ptrTime(finished), nil)
	failed.ErrorCode = "qc_timeout"
	done := dataflowStageFixture("decode", "SUCCEEDED",
		nil, ptrTime(now.Add(-10*time.Minute)), ptrTime(now.Add(-9*time.Minute)), nil)
	scans := []DataflowScanBlock{
		dataflowScanFixture("z9591", "FAILED", now.Add(-10*time.Minute), done, failed),
	}
	analyses := []DataflowAnalysisBlock{{
		AnalysisID: "a1", RunID: "r1", AnalysisTime: now.Add(-6 * time.Minute),
		GridID: "grid", Status: "ANALYSIS_READY", RadarCount: 4,
		CreatedAt: now.Add(-5 * time.Minute), Stages: []DataflowJobStage{},
	}}
	updated := now.Add(-4*time.Minute)
	forecasts := []DataflowForecastBlock{{
		RunID: "f1", IssueTime: now.Add(-6 * time.Minute), GridID: "grid",
		Status: "PUBLISHED", CreatedAt: now.Add(-6 * time.Minute), UpdatedAt: &updated,
		Stages: []DataflowJobStage{},
	}}
	events := AssembleDataflowSnapshot(now, now, time.Hour, scans, analyses, forecasts, nil, nil).Events
	if len(events) != 5 {
		t.Fatalf("event count = %d; want 5 (received, decode ok, qc failed, analysis created, publish)", len(events))
	}
	for index := 1; index < len(events); index++ {
		if events[index-1].Time.Before(events[index].Time) {
			t.Fatalf("events not newest first at %d: %+v", index, events)
		}
	}
	found := map[string]bool{}
	for _, event := range events {
		found[event.Kind] = true
		if event.Kind == "job.failed" && event.Detail != "qc_timeout" {
			t.Fatalf("failed event detail = %q; want qc_timeout", event.Detail)
		}
		if event.Kind == "scan.received" && event.RadarID != "z9591" {
			t.Fatalf("received event radar = %q", event.RadarID)
		}
	}
	for _, kind := range []string{"scan.received", "job.succeeded", "job.failed", "analysis.created", "run.published"} {
		if !found[kind] {
			t.Fatalf("missing event kind %s in %+v", kind, events)
		}
	}
}

func TestAssembleDataflowSnapshotRadarStatusQCDuration(t *testing.T) {
	now := dataflowTestClock()
	older := dataflowStageFixture("qc", "SUCCEEDED", nil,
		ptrTime(now.Add(-20*time.Minute)), ptrTime(now.Add(-19*time.Minute)), nil)
	newer := dataflowStageFixture("qc", "SUCCEEDED", nil,
		ptrTime(now.Add(-8*time.Minute)), ptrTime(now.Add(-7*time.Minute)), nil)
	runtime := int64(12345)
	withRuntime := newer
	withRuntime.RuntimeMS = &runtime
	scans := []DataflowScanBlock{
		dataflowScanFixture("z9591", "RADAR_GRID_READY", now.Add(-20*time.Minute), older),
		dataflowScanFixture("z9591", "RADAR_GRID_READY", now.Add(-8*time.Minute), withRuntime),
	}
	delay := int64(41)
	completeness := 0.99
	qi := 0.93
	statuses := []workflow.RadarStatusSummary{{
		RadarID: "z9591", Health: workflow.RadarHealthHealthy,
		ScanCompleteness: &completeness, MeanQualityIndex: &qi,
		DataDelaySeconds: &delay, ParticipatingInLatestAnalysis: true,
	}}
	snapshot := AssembleDataflowSnapshot(now, now, time.Hour, scans, nil, nil, statuses, nil)
	if len(snapshot.RadarStatuses) != 1 {
		t.Fatalf("radar status count = %d", len(snapshot.RadarStatuses))
	}
	status := snapshot.RadarStatuses[0]
	if status.QCDurationMS == nil || *status.QCDurationMS != 12345 {
		t.Fatalf("qc duration = %+v; want latest succeeded runtime 12345", status.QCDurationMS)
	}
	if status.Health != "HEALTHY" || !status.ParticipatingInLatestAnalysis || status.DataDelaySeconds == nil || *status.DataDelaySeconds != 41 {
		t.Fatalf("status passthrough broken: %+v", status)
	}
}

func TestAssembleDataflowSnapshotWindowMetadata(t *testing.T) {
	now := dataflowTestClock()
	snapshot := AssembleDataflowSnapshot(now, now, 3*time.Hour, nil, nil, nil, nil, []string{"analysis-cycles"})
	if snapshot.WindowMinutes != 180 || !snapshot.WindowStart.Equal(now.Add(-3*time.Hour)) {
		t.Fatalf("window metadata wrong: %+v", snapshot)
	}
	if snapshot.SchemaVersion != "1.0" || len(snapshot.Warnings) != 1 {
		t.Fatalf("schema version or warnings wrong: %+v", snapshot)
	}
	if len(snapshot.Stages) != 7 || snapshot.Stages[0].Key != "ingest" || snapshot.Stages[6].Key != "products" {
		t.Fatalf("strip shape wrong: %+v", snapshot.Stages)
	}
}

func ptrTime(value time.Time) *time.Time { return &value }

package operationalmetrics

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestRenderProducesStablePrometheusFamilies(t *testing.T) {
	delay := 42.5
	value := string(Render(Snapshot{
		Jobs:                  map[string]int64{"FAILED": 1, "SUCCEEDED": 4},
		RadarScans:            map[string]int64{"RADAR_GRID_READY": 2},
		RadarDataDelaySeconds: &delay,
	}, `build"one`))
	for _, expected := range []string{
		`rainpulse_build_info{version="build\"one"} 1`,
		`rainpulse_jobs{status="FAILED"} 1`,
		`rainpulse_jobs{status="SUCCEEDED"} 4`,
		`rainpulse_radar_data_delay_seconds 42.500`,
	} {
		if !strings.Contains(value, expected) {
			t.Fatalf("metrics do not contain %q:\n%s", expected, value)
		}
	}
}

func TestRenderProducesV11RadarAndAnalysisMetrics(t *testing.T) {
	delay := 721.25
	completeness := 0.962
	qcDuration := 4.75
	quality := 0.618
	interference := 0.031
	clutter := 0.024
	blockage := 0.11
	missing := 0.14
	analysisRadarCount := int64(2)
	analysisCoverage := 0.81
	publishDelay := 94.5

	value := string(Render(Snapshot{
		RadarScanReceivedTotal: 18,
		RadarDecodeFailedTotal: 2,
		Radars: map[string]RadarSnapshot{
			"z9598": {
				Lifecycle: "ready", LatestScanAvailable: true, DataDelaySeconds: &delay,
				ScanCompleteness: &completeness, QCDurationSeconds: &qcDuration,
				MeanQualityIndex: &quality, InterferenceRatio: &interference,
				ClutterRatio: &clutter, BlockageRatio: &blockage, MissingRatio: &missing,
			},
		},
		AnalysisRadarCount:          &analysisRadarCount,
		AnalysisValidCoverageRatio:  &analysisCoverage,
		AnalysisPublishDelaySeconds: &publishDelay,
		AnalysisOperationalEligible: true,
	}, "rp028-test"))

	for _, expected := range []string{
		`rainpulse_radar_scan_received_total 18`,
		`rainpulse_radar_decode_failed_total 2`,
		`rainpulse_radar_latest_scan_available{lifecycle="ready",radar_id="z9598"} 1`,
		`rainpulse_radar_data_delay_seconds{lifecycle="ready",radar_id="z9598"} 721.250`,
		`rainpulse_radar_scan_completeness{lifecycle="ready",radar_id="z9598"} 0.962`,
		`rainpulse_radar_qc_duration_seconds{lifecycle="ready",radar_id="z9598"} 4.750`,
		`rainpulse_radar_qi_mean{lifecycle="ready",radar_id="z9598"} 0.618`,
		`rainpulse_radar_interference_ratio{lifecycle="ready",radar_id="z9598"} 0.031`,
		`rainpulse_radar_clutter_ratio{lifecycle="ready",radar_id="z9598"} 0.024`,
		`rainpulse_radar_blockage_ratio{lifecycle="ready",radar_id="z9598"} 0.110`,
		`rainpulse_radar_missing_ratio{lifecycle="ready",radar_id="z9598"} 0.140`,
		`rainpulse_analysis_radar_count{operational_eligible="true"} 2`,
		`rainpulse_analysis_valid_coverage_ratio{operational_eligible="true"} 0.810`,
		`rainpulse_analysis_publish_delay_seconds{operational_eligible="true"} 94.500`,
		`# TYPE rainpulse_qpe_station_bias gauge`,
	} {
		if !strings.Contains(value, expected) {
			t.Fatalf("metrics do not contain %q:\n%s", expected, value)
		}
	}
	for _, line := range strings.Split(value, "\n") {
		if strings.HasPrefix(line, "rainpulse_qpe_station_bias") {
			t.Fatalf("station bias sample must remain absent until gauge adjustment exists: %s", line)
		}
	}
}

func TestHandlerFailsClosedWhenMetricsAreUnavailable(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	response := httptest.NewRecorder()
	Handler(failingProvider{}, "rp028-test").ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected metrics failure to return 503, got %d", response.Code)
	}
}

func TestRenderProducesCorrelatableJobAndOutboxMetrics(t *testing.T) {
	value := string(Render(Snapshot{
		ActiveJobs: []JobMetric{{
			JobID: "job-1", RunID: "run-1", JobType: "radar.qc", Status: "RUNNING",
			AgeSeconds: 721.25,
		}},
		RecentFailedJobs: []JobMetric{{
			JobID: "job-2", RunID: "run-2", JobType: "analysis.qpe", ErrorCode: "BAD_INPUT",
			OccurredAt: time.Unix(1_787_900_000, 500_000_000),
		}},
		OutboxIssues: []OutboxMetric{{
			EventID: "event-1", AggregateID: "job-2", EventType: "job.requested.v1",
			Subject: "rainpulse.jobs.analysis.qpe.requested.v1", PendingSeconds: 83.75,
		}},
	}, "rp030-test"))

	for _, expected := range []string{
		`rainpulse_job_active_seconds{job_id="job-1",job_type="radar.qc",run_id="run-1",status="RUNNING"} 721.250`,
		`rainpulse_job_failure_timestamp_seconds{error_code="BAD_INPUT",job_id="job-2",job_type="analysis.qpe",run_id="run-2"} 1787900000.500`,
		`rainpulse_outbox_event_pending_seconds{aggregate_id="job-2",event_id="event-1",event_type="job.requested.v1",subject="rainpulse.jobs.analysis.qpe.requested.v1"} 83.750`,
	} {
		if !strings.Contains(value, expected) {
			t.Fatalf("metrics do not contain %q:\n%s", expected, value)
		}
	}
}

func TestOperationalAlertsOnlyTargetEnabledBusinessInputs(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "deploy", "observability", "rainpulse-alerts.yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read alert rules: %v", err)
	}
	rules := string(raw)
	for _, expected := range []string{
		`rainpulse_radar_data_delay_seconds{lifecycle="ready"}`,
		`rainpulse_radar_latest_scan_available{lifecycle="ready"}`,
		`rainpulse_radar_scan_completeness{lifecycle="ready"}`,
		`rainpulse_radar_qi_mean{lifecycle="ready"}`,
		`rainpulse_radar_interference_ratio{lifecycle="ready"}`,
		`rainpulse_analysis_valid_coverage_ratio{operational_eligible="true"}`,
	} {
		if !strings.Contains(rules, expected) {
			t.Fatalf("alert rules do not contain business gate %q", expected)
		}
	}
}

type failingProvider struct{}

func (failingProvider) OperationalMetrics(context.Context) (Snapshot, error) {
	return Snapshot{}, errors.New("database unavailable")
}

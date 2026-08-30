package operationalmetrics

import (
	"context"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"
)

type Snapshot struct {
	Jobs                        map[string]int64
	RadarScans                  map[string]int64
	AnalysisCycles              map[string]int64
	ForecastRuns                map[string]int64
	Outbox                      map[string]int64
	RadarScanReceivedTotal      int64
	RadarDecodeFailedTotal      int64
	Radars                      map[string]RadarSnapshot
	RadarDataDelaySeconds       *float64
	OldestPendingSeconds        *float64
	AnalysisRadarCount          *int64
	AnalysisValidCoverageRatio  *float64
	AnalysisPublishDelaySeconds *float64
	AnalysisOperationalEligible bool
	QPEStationBias              *float64
	ActiveJobs                  []JobMetric
	RecentFailedJobs            []JobMetric
	OutboxIssues                []OutboxMetric
}

type JobMetric struct {
	JobID      string
	RunID      string
	JobType    string
	Status     string
	ErrorCode  string
	AgeSeconds float64
	OccurredAt time.Time
}

type OutboxMetric struct {
	EventID        string
	AggregateID    string
	EventType      string
	Subject        string
	PendingSeconds float64
}

type RadarSnapshot struct {
	Lifecycle           string
	LatestScanAvailable bool
	DataDelaySeconds    *float64
	ScanCompleteness    *float64
	QCDurationSeconds   *float64
	MeanQualityIndex    *float64
	InterferenceRatio   *float64
	ClutterRatio        *float64
	BlockageRatio       *float64
	MissingRatio        *float64
}

type Provider interface {
	OperationalMetrics(context.Context) (Snapshot, error)
}

func Handler(provider Provider, version string) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet {
			response.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		snapshot, err := provider.OperationalMetrics(request.Context())
		if err != nil {
			http.Error(response, "metrics unavailable", http.StatusServiceUnavailable)
			return
		}
		response.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		_, _ = response.Write(Render(snapshot, version))
	})
}

func Render(snapshot Snapshot, version string) []byte {
	var output strings.Builder
	fmt.Fprintf(
		&output,
		"# HELP rainpulse_build_info RainPulse build identity.\n"+
			"# TYPE rainpulse_build_info gauge\n"+
			"rainpulse_build_info{version=\"%s\"} 1\n",
		escapeLabel(version),
	)
	writeStatusFamily(&output, "rainpulse_jobs", "Persisted jobs by status.", snapshot.Jobs)
	writeStatusFamily(
		&output, "rainpulse_radar_scans", "Radar scan workflows by status.", snapshot.RadarScans,
	)
	writeStatusFamily(
		&output,
		"rainpulse_analysis_cycles",
		"Radar analysis cycles by status.",
		snapshot.AnalysisCycles,
	)
	writeStatusFamily(
		&output, "rainpulse_forecast_runs", "Forecast runs by status.", snapshot.ForecastRuns,
	)
	writeStatusFamily(&output, "rainpulse_outbox_events", "Outbox events by status.", snapshot.Outbox)
	writeCounter(
		&output, "rainpulse_radar_scan_received_total",
		"Total immutable radar volumes registered by the control plane.",
		snapshot.RadarScanReceivedTotal,
	)
	writeCounter(
		&output, "rainpulse_radar_decode_failed_total",
		"Total failed radar decode jobs retained by the control plane.",
		snapshot.RadarDecodeFailedTotal,
	)
	writeRadarAvailabilityGauge(
		&output, "rainpulse_radar_latest_scan_available",
		"Whether a latest registered volume exists for each configured radar.", snapshot.Radars,
	)
	if len(snapshot.Radars) == 0 {
		writeOptionalGauge(
			&output,
			"rainpulse_radar_data_delay_seconds",
			"Age of the newest registered radar volume end time.",
			snapshot.RadarDataDelaySeconds,
		)
	} else {
		writeRadarGauge(&output, "rainpulse_radar_data_delay_seconds", "Age of the latest registered radar volume end time by radar.", snapshot.Radars, func(value RadarSnapshot) *float64 { return value.DataDelaySeconds })
	}
	writeRadarGauge(&output, "rainpulse_radar_scan_completeness", "Latest volume completeness ratio by radar.", snapshot.Radars, func(value RadarSnapshot) *float64 { return value.ScanCompleteness })
	writeRadarGauge(&output, "rainpulse_radar_qc_duration_seconds", "Latest completed polar quality-control job duration by radar.", snapshot.Radars, func(value RadarSnapshot) *float64 { return value.QCDurationSeconds })
	writeRadarGauge(&output, "rainpulse_radar_qi_mean", "Latest mean radar quality index by radar.", snapshot.Radars, func(value RadarSnapshot) *float64 { return value.MeanQualityIndex })
	writeRadarGauge(&output, "rainpulse_radar_interference_ratio", "Latest radial-interference ray ratio by radar.", snapshot.Radars, func(value RadarSnapshot) *float64 { return value.InterferenceRatio })
	writeRadarGauge(&output, "rainpulse_radar_clutter_ratio", "Latest ground, sea and anomalous-propagation clutter gate ratio by radar.", snapshot.Radars, func(value RadarSnapshot) *float64 { return value.ClutterRatio })
	writeRadarGauge(&output, "rainpulse_radar_blockage_ratio", "Latest beam-blocked grid-cell ratio by radar.", snapshot.Radars, func(value RadarSnapshot) *float64 { return value.BlockageRatio })
	writeRadarGauge(&output, "rainpulse_radar_missing_ratio", "Latest missing grid-cell ratio by radar.", snapshot.Radars, func(value RadarSnapshot) *float64 { return value.MissingRatio })
	writeOptionalGauge(
		&output,
		"rainpulse_oldest_pending_job_seconds",
		"Age of the oldest pending or running job.",
		snapshot.OldestPendingSeconds,
	)
	writeOptionalLabeledIntGauge(
		&output, "rainpulse_analysis_radar_count",
		"Number of contributing radars in the latest completed analysis.",
		snapshot.AnalysisRadarCount, snapshot.AnalysisOperationalEligible,
	)
	writeOptionalLabeledGauge(
		&output, "rainpulse_analysis_valid_coverage_ratio",
		"Valid coverage ratio of the latest completed analysis.",
		snapshot.AnalysisValidCoverageRatio, snapshot.AnalysisOperationalEligible,
	)
	writeOptionalLabeledGauge(
		&output, "rainpulse_analysis_publish_delay_seconds",
		"Delay from analysis time to the latest completed QPE evidence.",
		snapshot.AnalysisPublishDelaySeconds, snapshot.AnalysisOperationalEligible,
	)
	writeOptionalGauge(
		&output, "rainpulse_qpe_station_bias",
		"Latest QPE station bias when gauge adjustment is enabled.",
		snapshot.QPEStationBias,
	)
	writeJobAgeMetrics(&output, snapshot.ActiveJobs)
	writeJobFailureMetrics(&output, snapshot.RecentFailedJobs)
	writeOutboxAgeMetrics(&output, snapshot.OutboxIssues)
	return []byte(output.String())
}

func writeJobAgeMetrics(output *strings.Builder, values []JobMetric) {
	const name = "rainpulse_job_active_seconds"
	fmt.Fprintf(output, "# HELP %s Age of each pending or running job.\n# TYPE %s gauge\n", name, name)
	for _, value := range values {
		fmt.Fprintf(
			output,
			"%s{job_id=\"%s\",job_type=\"%s\",run_id=\"%s\",status=\"%s\"} %.3f\n",
			name, escapeLabel(value.JobID), escapeLabel(value.JobType), escapeLabel(value.RunID),
			escapeLabel(value.Status), value.AgeSeconds,
		)
	}
}

func writeJobFailureMetrics(output *strings.Builder, values []JobMetric) {
	const name = "rainpulse_job_failure_timestamp_seconds"
	fmt.Fprintf(output, "# HELP %s Completion timestamp of each recent failed job.\n# TYPE %s gauge\n", name, name)
	for _, value := range values {
		fmt.Fprintf(
			output,
			"%s{error_code=\"%s\",job_id=\"%s\",job_type=\"%s\",run_id=\"%s\"} %.3f\n",
			name, escapeLabel(value.ErrorCode), escapeLabel(value.JobID),
			escapeLabel(value.JobType), escapeLabel(value.RunID),
			float64(value.OccurredAt.UTC().UnixMilli())/1000,
		)
	}
}

func writeOutboxAgeMetrics(output *strings.Builder, values []OutboxMetric) {
	const name = "rainpulse_outbox_event_pending_seconds"
	fmt.Fprintf(output, "# HELP %s Age of each unpublished outbox event.\n# TYPE %s gauge\n", name, name)
	for _, value := range values {
		fmt.Fprintf(
			output,
			"%s{aggregate_id=\"%s\",event_id=\"%s\",event_type=\"%s\",subject=\"%s\"} %.3f\n",
			name, escapeLabel(value.AggregateID), escapeLabel(value.EventID),
			escapeLabel(value.EventType), escapeLabel(value.Subject), value.PendingSeconds,
		)
	}
}

func writeCounter(output *strings.Builder, name, help string, value int64) {
	fmt.Fprintf(output, "# HELP %s %s\n# TYPE %s counter\n%s %d\n", name, help, name, name, value)
}

func writeRadarAvailabilityGauge(
	output *strings.Builder,
	name string,
	help string,
	values map[string]RadarSnapshot,
) {
	fmt.Fprintf(output, "# HELP %s %s\n# TYPE %s gauge\n", name, help, name)
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, radarID := range keys {
		value := values[radarID]
		available := 0
		if value.LatestScanAvailable {
			available = 1
		}
		fmt.Fprintf(
			output, "%s{lifecycle=\"%s\",radar_id=\"%s\"} %d\n",
			name, escapeLabel(value.Lifecycle), escapeLabel(radarID), available,
		)
	}
}

func writeStatusFamily(output *strings.Builder, name, help string, values map[string]int64) {
	fmt.Fprintf(output, "# HELP %s %s\n# TYPE %s gauge\n", name, help, name)
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		fmt.Fprintf(output, "%s{status=\"%s\"} %d\n", name, escapeLabel(key), values[key])
	}
}

func writeOptionalGauge(output *strings.Builder, name, help string, value *float64) {
	fmt.Fprintf(output, "# HELP %s %s\n# TYPE %s gauge\n", name, help, name)
	if value != nil {
		fmt.Fprintf(output, "%s %.3f\n", name, *value)
	}
}

func writeRadarGauge(
	output *strings.Builder,
	name string,
	help string,
	values map[string]RadarSnapshot,
	selectValue func(RadarSnapshot) *float64,
) {
	fmt.Fprintf(output, "# HELP %s %s\n# TYPE %s gauge\n", name, help, name)
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, radarID := range keys {
		value := values[radarID]
		metric := selectValue(value)
		if metric == nil {
			continue
		}
		fmt.Fprintf(
			output, "%s{lifecycle=\"%s\",radar_id=\"%s\"} %.3f\n",
			name, escapeLabel(value.Lifecycle), escapeLabel(radarID), *metric,
		)
	}
}

func writeOptionalLabeledGauge(
	output *strings.Builder,
	name string,
	help string,
	value *float64,
	operationalEligible bool,
) {
	fmt.Fprintf(output, "# HELP %s %s\n# TYPE %s gauge\n", name, help, name)
	if value != nil {
		fmt.Fprintf(
			output, "%s{operational_eligible=\"%t\"} %.3f\n",
			name, operationalEligible, *value,
		)
	}
}

func writeOptionalLabeledIntGauge(
	output *strings.Builder,
	name string,
	help string,
	value *int64,
	operationalEligible bool,
) {
	fmt.Fprintf(output, "# HELP %s %s\n# TYPE %s gauge\n", name, help, name)
	if value != nil {
		fmt.Fprintf(
			output, "%s{operational_eligible=\"%t\"} %d\n",
			name, operationalEligible, *value,
		)
	}
}

func escapeLabel(value string) string {
	value = strings.ReplaceAll(value, `\`, `\\`)
	value = strings.ReplaceAll(value, "\n", `\n`)
	return strings.ReplaceAll(value, `"`, `\"`)
}

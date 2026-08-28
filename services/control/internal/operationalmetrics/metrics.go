package operationalmetrics

import (
	"context"
	"fmt"
	"net/http"
	"sort"
	"strings"
)

type Snapshot struct {
	Jobs                  map[string]int64
	RadarScans            map[string]int64
	AnalysisCycles        map[string]int64
	ForecastRuns          map[string]int64
	Outbox                map[string]int64
	RadarDataDelaySeconds *float64
	OldestPendingSeconds  *float64
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
	writeOptionalGauge(
		&output,
		"rainpulse_radar_data_delay_seconds",
		"Age of the newest registered radar volume end time.",
		snapshot.RadarDataDelaySeconds,
	)
	writeOptionalGauge(
		&output,
		"rainpulse_oldest_pending_job_seconds",
		"Age of the oldest pending or running job.",
		snapshot.OldestPendingSeconds,
	)
	return []byte(output.String())
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

func escapeLabel(value string) string {
	value = strings.ReplaceAll(value, `\`, `\\`)
	value = strings.ReplaceAll(value, "\n", `\n`)
	return strings.ReplaceAll(value, `"`, `\"`)
}

package operationalmetrics

import (
	"strings"
	"testing"
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

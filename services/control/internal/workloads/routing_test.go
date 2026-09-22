package workloads

import (
	"testing"
	"time"
)

func TestResourceRoutes(t *testing.T) {
	now := time.Date(2026, 9, 22, 10, 0, 0, 0, time.UTC)
	settings := Settings{Enabled: true, RealtimeAge: time.Hour, FutureSkew: 6 * time.Minute}
	cases := []struct {
		name, subject string
		meta          Metadata
		lane, reason  string
	}{
		{"fresh", RequestPrefix + "radar_qc", Metadata{now, now.Add(-5 * time.Minute), false}, "realtime", "fresh_input"},
		{"boundary", RequestPrefix + "radar_qc", Metadata{now, now.Add(-time.Hour), false}, "realtime", "fresh_input"},
		{"old", RequestPrefix + "radar_qc", Metadata{now, now.Add(-time.Hour - time.Nanosecond), false}, "background", "historical_at_creation"},
		{"manual", RequestPrefix + "radar_qc", Metadata{now, now, true}, "background", "explicit_regeneration"},
		{"diagnostic", RequestPrefix + "analysis_diagnostics", Metadata{now, now, false}, "background", "noncritical_stage"},
		{"verify", RequestPrefix + "forecast_verification", Metadata{now, now, false}, "background", "noncritical_stage"},
		{"unknown", RequestPrefix + "radar_qc", Metadata{}, "background", "unknown_source_time"},
		{"future", RequestPrefix + "radar_qc", Metadata{now, now.Add(7 * time.Minute), false}, "background", "future_source_time"},
		{"gpu", RequestPrefix + "nowcastnet_shadow", Metadata{now, now, false}, "unmanaged", "existing_unmanaged_route"},
		{"synthetic", RequestPrefix + "radar_qc_synthetic", Metadata{}, "unmanaged", "existing_unmanaged_route"},
		{"result", "rainpulse.jobs.results.completed", Metadata{}, "unmanaged", "existing_unmanaged_route"},
		{"persisted", BackgroundPrefix + "radar_qc", Metadata{now, now, false}, "background", "persisted_route"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := Select(c.subject, c.meta, settings)
			if got.Lane != c.lane || got.Reason != c.reason {
				t.Fatalf("decision=%+v", got)
			}
			if got.Lane == "realtime" && got.Subject != c.subject {
				t.Fatal("changed realtime subject")
			}
			again := Select(got.Subject, c.meta, settings)
			if again.Subject != got.Subject {
				t.Fatal("route is not stable")
			}
		})
	}
}
func TestAllCPUStagesHaveDisjointBackgroundRoutes(t *testing.T) {
	for stage := range cpuStages {
		t.Run(stage, func(t *testing.T) {
			base := RequestPrefix + stage
			got := Select(base, Metadata{Regeneration: true}, Settings{Enabled: true})
			if got.Subject != BackgroundPrefix+stage {
				t.Fatalf("route=%+v", got)
			}
			if value, ok := BaseSubject(got.Subject); !ok || value != base {
				t.Fatal("canonicalization failed")
			}
		})
	}
}
func TestDisabledDoesNotMoveLegacyWork(t *testing.T) {
	got := Select(RequestPrefix+"radar_qc", Metadata{Regeneration: true}, Settings{})
	if got.Lane != "realtime" {
		t.Fatal(got)
	}
	got = Select(BackgroundPrefix+"radar_qc", Metadata{}, Settings{})
	if got.Lane != "background" {
		t.Fatal("disabled flag moved persisted background work")
	}
}
func TestRoutingEnvironment(t *testing.T) {
	t.Setenv("RAINPULSE_RESOURCE_LANES_ENABLED", "false")
	t.Setenv("RAINPULSE_RESOURCE_REALTIME_MAX_AGE", "1h")
	got, err := SettingsFromEnvironment()
	if err != nil || got.Enabled || got.RealtimeAge != time.Hour {
		t.Fatalf("%+v %v", got, err)
	}
	t.Setenv("RAINPULSE_RESOURCE_LANES_ENABLED", "true")
	got, err = SettingsFromEnvironment()
	if err != nil || !got.Enabled {
		t.Fatalf("%+v %v", got, err)
	}
	for _, value := range []string{"0s", "-1s", "25h", "bad"} {
		t.Run(value, func(t *testing.T) {
			t.Setenv("RAINPULSE_RESOURCE_REALTIME_MAX_AGE", value)
			if _, err := SettingsFromEnvironment(); err == nil {
				t.Fatal("invalid age accepted")
			}
		})
	}
	t.Setenv("RAINPULSE_RESOURCE_LANES_ENABLED", "wrong")
	if _, err := SettingsFromEnvironment(); err == nil {
		t.Fatal("invalid flag accepted")
	}
}

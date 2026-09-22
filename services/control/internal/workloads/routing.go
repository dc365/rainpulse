// Package workloads describes transport/resource isolation, never data eligibility.
package workloads

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

const RequestPrefix = "rainpulse.jobs.requested."
const BackgroundPrefix = RequestPrefix + "background."

// Keep current subjects as the realtime lane. Existing consumers and persisted
// legacy requests continue to work; background consumers cannot steal them.
var cpuStages = map[string]bool{
	"radar_decode": true, "radar_qc": true, "radar_grid": true,
	"analysis_mosaic": true, "analysis_qpe": true, "analysis_diagnostics": true,
	"nowcast_input": true, "pysteps_lk": true, "pysteps_lk.v2": true,
	"product_build": true, "forecast_verification": true,
}

type Settings struct {
	Enabled     bool
	RealtimeAge time.Duration
	FutureSkew  time.Duration
}

func SettingsFromEnvironment() (Settings, error) {
	enabled, err := strconv.ParseBool(env("RAINPULSE_RESOURCE_LANES_ENABLED", "false"))
	if err != nil {
		return Settings{}, fmt.Errorf("invalid RAINPULSE_RESOURCE_LANES_ENABLED: %w", err)
	}
	age, err := time.ParseDuration(env("RAINPULSE_RESOURCE_REALTIME_MAX_AGE", "1h"))
	if err != nil || age <= 0 || age > 24*time.Hour {
		return Settings{}, fmt.Errorf("RAINPULSE_RESOURCE_REALTIME_MAX_AGE must be in (0,24h]")
	}
	return Settings{Enabled: enabled, RealtimeAge: age, FutureSkew: 6 * time.Minute}, nil
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

// BaseSubject returns false for results, GPU/offline adapters, and simulations.
// Unsupported stages remain on their existing route, not a queue without workers.
func BaseSubject(subject string) (string, bool) {
	base := subject
	if strings.HasPrefix(base, BackgroundPrefix) {
		base = RequestPrefix + strings.TrimPrefix(base, BackgroundPrefix)
	}
	if !strings.HasPrefix(base, RequestPrefix) || !cpuStages[strings.TrimPrefix(base, RequestPrefix)] {
		return "", false
	}
	return base, true
}

type Metadata struct {
	CreatedAt    time.Time // persisted job creation time, NOT current wall clock
	SourceTime   time.Time // immutable scan end / analysis time / forecast issue
	Regeneration bool
}

type Decision struct {
	Subject string
	Lane    string
	Reason  string
}

func Select(subject string, metadata Metadata, settings Settings) Decision {
	base, supported := BaseSubject(subject)
	if !supported {
		return Decision{subject, "unmanaged", "existing_unmanaged_route"}
	}
	if strings.HasPrefix(subject, BackgroundPrefix) {
		return Decision{subject, "background", "persisted_route"}
	}
	if !settings.Enabled {
		return Decision{base, "realtime", "lanes_disabled"}
	}
	reason := ""
	switch {
	case metadata.Regeneration:
		reason = "explicit_regeneration"
	case base == RequestPrefix+"analysis_diagnostics" || base == RequestPrefix+"forecast_verification":
		reason = "noncritical_stage"
	case metadata.CreatedAt.IsZero() || metadata.SourceTime.IsZero():
		reason = "unknown_source_time"
	case metadata.SourceTime.After(metadata.CreatedAt.Add(settings.FutureSkew)):
		reason = "future_source_time"
	case metadata.CreatedAt.Sub(metadata.SourceTime) > settings.RealtimeAge:
		reason = "historical_at_creation"
	default:
		return Decision{base, "realtime", "fresh_input"}
	}
	return Decision{BackgroundPrefix + strings.TrimPrefix(base, RequestPrefix), "background", reason}
}

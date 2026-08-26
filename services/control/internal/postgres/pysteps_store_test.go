package postgres

import (
	"bytes"
	"encoding/json"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

func TestValidatePystepsLKMetricsAcceptsActiveMissingPolicy(t *testing.T) {
	issueTime := time.Date(2026, 8, 25, 12, 10, 0, 0, time.UTC)
	metrics := workflow.PystepsLKMetrics{
		SchemaVersion: "1.0",
		RunID:         uuid.MustParse("a78aa324-0832-59e1-b9ea-d97933b2821e"),
		JobID:         uuid.MustParse("81400000-0000-4000-8000-000000000002"),
		IssueTime:     issueTime,
		GridID:        "fuzhou_118_123_25_27_0p01deg_v1",
		ModelID:       orchestration.PystepsLKModelID,
		ModelVersion:  orchestration.PystepsLKModelVersion,
		ConfigVersion: "rp016-pysteps-lk-v1",
		InputURI:      "s3://rainpulse/nowcast-input/input.zarr",
		InputAssetIDs: []uuid.UUID{
			uuid.MustParse("81300000-0000-4000-8000-000000000001"),
		},
		LeadCount:                       24,
		LeadStepMinutes:                 5,
		ValidFrom:                       issueTime.Add(5 * time.Minute),
		ValidTo:                         issueTime.Add(120 * time.Minute),
		MotionFeatureCount:              4,
		MotionValidFraction:             0.7,
		MissingBufferPixels:             5,
		ConfidenceKind:                  orchestration.PystepsLKConfidenceKind,
		TrackableRainPixelCount:         64,
		FirstLeadValidCoverageRatio:     0.9,
		LastLeadValidCoverageRatio:      0.7,
		MaximumForecastRateMMH:          20,
		BaselineModels:                  []string{"persistence", "translation"},
		MissingPolicy:                   "nearest_valid_buffer_preserve_advected_mask",
		RuntimeMS:                       100,
		GlobalTranslationXPixelsPerStep: 1,
		GlobalTranslationYPixelsPerStep: 0,
		MeanMotionUMS:                   3,
		MeanMotionVMS:                   0,
		MeasuredAt:                      issueTime.Add(time.Minute),
	}
	event := orchestration.JobCompleted{
		Payload: orchestration.JobCompletedPayload{RuntimeMS: 200},
	}

	if err := validatePystepsLKMetrics(metrics, event); err != nil {
		t.Fatalf("active RP-016 metrics rejected: %v", err)
	}
	encoded, err := json.Marshal(metrics)
	if err != nil {
		t.Fatalf("marshal active RP-016 metrics: %v", err)
	}
	for _, field := range []string{
		`"motion_feature_count":4`,
		`"motion_valid_fraction":0.7`,
		`"missing_buffer_pixels":5`,
		`"confidence_kind":"technical_forecast_quality_index_not_calibrated_probability"`,
	} {
		if !json.Valid(encoded) || !bytes.Contains(encoded, []byte(field)) {
			t.Fatalf("persisted diagnostics omit %s: %s", field, encoded)
		}
	}
	metrics.ConfidenceKind = "calibrated_probability"
	if err := validatePystepsLKMetrics(metrics, event); err == nil {
		t.Fatal("probabilistic confidence label must be rejected for RP-016")
	}
	metrics.ConfidenceKind = orchestration.PystepsLKConfidenceKind

	metrics.ModelVersion = "pysteps-lk-1.0.0"
	metrics.ConfigVersion = "rp014-pysteps-lk-v1"
	metrics.MissingPolicy = "dry_floor_working_copy_preserve_advected_mask"
	metrics.MotionFeatureCount = 0
	metrics.MotionValidFraction = 0
	metrics.MissingBufferPixels = 0
	metrics.ConfidenceKind = ""
	if err := validatePystepsLKMetrics(metrics, event); err != nil {
		t.Fatalf("in-flight RP-014 metrics rejected before request identity check: %v", err)
	}

	metrics.MissingPolicy = "unknown"
	if err := validatePystepsLKMetrics(metrics, event); err == nil {
		t.Fatal("unknown missing-data policy must be rejected")
	}
}

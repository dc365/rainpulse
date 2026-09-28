package orchestration

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

func TestDraftSDecodeRebuildInputRequiresFrozenRun(t *testing.T) {
	input := RadarDecodeInput{
		RadarID: "z9598", Lifecycle: workflow.RadarDraft,
		ConfigVersion: "z9598-fmt-v1", Config: json.RawMessage(`{"hardware":{"radar_band":"S"}}`),
		ConfigSHA256: strings.Repeat("a", 64), SourceFormat: "cma-rstm-level2",
		InputURI:    "s3://rainpulse/radar/raw/z9598/immutable.bz2",
		InputSHA256: strings.Repeat("b", 64), InputSizeBytes: 10,
		VolumeStartTime: time.Now().Add(-time.Minute), VolumeEndTime: time.Now(),
		ExistingRunID: uuid.New(), RebuildID: uuid.New(),
	}
	if err := validateRadarDecodeInput(input); err != nil {
		t.Fatalf("draft S recovery rejected: %v", err)
	}
	input.Lifecycle = workflow.RadarReady
	if err := validateRadarDecodeInput(input); err == nil {
		t.Fatal("ready S station accepted")
	}
	input.Lifecycle = workflow.RadarDraft
	input.RebuildID = uuid.Nil
	if err := validateRadarDecodeInput(input); err == nil {
		t.Fatal("missing rebuild identity accepted")
	}
}

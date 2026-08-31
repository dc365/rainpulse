package postgres

import (
	"context"
	"strings"
	"testing"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
)

func TestListRadarScansPageRejectsNegativeOffset(t *testing.T) {
	store := &Store{}
	_, err := store.ListRadarScansPage(context.Background(), 20, -1, nil, nil)
	if err == nil || !strings.Contains(err.Error(), "offset") {
		t.Fatalf("negative page offset error = %v", err)
	}
}

func TestRadarScanCanCreateVersionedQCFromCompletedGrid(t *testing.T) {
	if !radarScanCanCreateQC(workflow.RadarScanGridReady) {
		t.Fatal("completed RadarGrid must permit a new immutable QC version")
	}
	if radarScanCanCreateQC(workflow.RadarScanRawReceived) {
		t.Fatal("raw scan must not bypass normalization before QC")
	}
}

package postgres

import (
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"testing"
)

func TestUnknownGeometryRequiresUnavailableDraftEvidence(t *testing.T) {
	h := workflow.RadarHealthMetrics{Health: workflow.RadarHealthUnavailable, HealthReasons: []string{"SCAN_GEOMETRY_UNKNOWN"}}
	if !unknownDraftGeometry(h, true) {
		t.Fatal("draft unknown geometry rejected")
	}
	if unknownDraftGeometry(h, false) {
		t.Fatal("ready geometry admitted")
	}
	h.Health = workflow.RadarHealthHealthy
	if unknownDraftGeometry(h, true) {
		t.Fatal("healthy unknown geometry admitted")
	}
	h.Health = workflow.RadarHealthUnavailable
	h.ScanCompleteness = 1
	if unknownDraftGeometry(h, true) {
		t.Fatal("complete unknown geometry admitted")
	}
	h.ScanCompleteness = 0
	h.HealthReasons = nil
	if unknownDraftGeometry(h, true) {
		t.Fatal("missing evidence admitted")
	}
}

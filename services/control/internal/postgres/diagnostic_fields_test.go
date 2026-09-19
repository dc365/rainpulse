package postgres

import (
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"testing"
)

func TestPolarDiagnosticsAcceptAllSweepsButRejectDuplicatesAndIncompleteCuts(t *testing.T) {
	p := newPolarDiagnosticFields()
	radar, scan, elevation, distance := "z9598", uuid.New(), 0.5, 330.0
	var layer workflow.DiagnosticLayer
	for _, sweep := range []int{0, 4} {
		for _, field := range []string{"DBZH_RAW", "DBZH_QC", "QUALITY_INDEX", "QC_FLAGS"} {
			layer = workflow.DiagnosticLayer{RadarID: &radar, ScanID: &scan, SweepNumber: &sweep, ElevationDeg: &elevation, MaximumRangeKM: &distance, Field: field}
			if err := p.add(layer); err != nil {
				t.Fatal(err)
			}
		}
	}
	expected := map[string]uuid.UUID{radar: scan}
	if err := p.validate(expected); err != nil {
		t.Fatal(err)
	}
	if err := p.add(layer); err == nil {
		t.Fatal("duplicate in the same cut accepted")
	}
	delete(p.fields[radar][4], "DBZH_QC")
	if err := p.validate(expected); err == nil {
		t.Fatal("another sweep hid incomplete QC")
	}
	other := uuid.New()
	layer.ScanID = &other
	if err := p.add(layer); err == nil {
		t.Fatal("mixed scans accepted")
	}
}

package postgres

import (
	"fmt"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"math"
)

type polarDiagnosticFields struct {
	scans  map[string]uuid.UUID
	fields map[string]map[int]map[string]struct{}
}

func newPolarDiagnosticFields() *polarDiagnosticFields {
	return &polarDiagnosticFields{scans: map[string]uuid.UUID{}, fields: map[string]map[int]map[string]struct{}{}}
}
func (p *polarDiagnosticFields) add(layer workflow.DiagnosticLayer) error {
	if layer.RadarID == nil || *layer.RadarID == "" || layer.ScanID == nil ||
		layer.SweepNumber == nil || *layer.SweepNumber < 0 || layer.ElevationDeg == nil ||
		math.IsNaN(*layer.ElevationDeg) || math.IsInf(*layer.ElevationDeg, 0) ||
		layer.MaximumRangeKM == nil || *layer.MaximumRangeKM <= 0 || math.IsNaN(*layer.MaximumRangeKM) || math.IsInf(*layer.MaximumRangeKM, 0) {
		return fmt.Errorf("%w: invalid polar diagnostic layer", orchestration.ErrInvalidEvent)
	}
	radar, sweep := *layer.RadarID, *layer.SweepNumber
	if scan, exists := p.scans[radar]; exists && scan != *layer.ScanID {
		return fmt.Errorf("%w: inconsistent polar diagnostic scan", orchestration.ErrInvalidEvent)
	}
	p.scans[radar] = *layer.ScanID
	if p.fields[radar] == nil {
		p.fields[radar] = map[int]map[string]struct{}{}
	}
	if p.fields[radar][sweep] == nil {
		p.fields[radar][sweep] = map[string]struct{}{}
	}
	fields := p.fields[radar][sweep]
	if _, exists := fields[layer.Field]; exists {
		return fmt.Errorf("%w: duplicate polar diagnostic field in sweep", orchestration.ErrInvalidEvent)
	}
	fields[layer.Field] = struct{}{}
	return nil
}
func (p *polarDiagnosticFields) validate(expected map[string]uuid.UUID) error {
	if len(p.fields) != len(expected) {
		return fmt.Errorf("%w: polar diagnostics do not match requested radars", orchestration.ErrInvalidEvent)
	}
	required := []string{"DBZH_RAW", "DBZH_QC", "QUALITY_INDEX", "QC_FLAGS"}
	for radar, scan := range expected {
		sweeps := p.fields[radar]
		if len(sweeps) == 0 || p.scans[radar] != scan {
			return fmt.Errorf("%w: polar diagnostics do not match requested radar", orchestration.ErrInvalidEvent)
		}
		for _, fields := range sweeps {
			if len(fields) != len(required) {
				return fmt.Errorf("%w: incomplete polar diagnostic sweep", orchestration.ErrInvalidEvent)
			}
			for _, field := range required {
				if _, ok := fields[field]; !ok {
					return fmt.Errorf("%w: missing polar diagnostic field", orchestration.ErrInvalidEvent)
				}
			}
		}
	}
	return nil
}

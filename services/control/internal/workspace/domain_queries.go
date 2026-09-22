package workspace

import (
	"context"
	"fmt"
	"math"
	"strconv"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

// These methods return the existing workspace view, without serializing a
// domain API response. The nil-query path is only for legacy/test constructors.
// A failed injected service NEVER falls back to the core HTTP handler.
func (h *Handler) queryForecastPage(ctx context.Context, status, cursor string) (forecastRunPage, error) {
	if h.queries == nil {
		return h.legacyForecastPage(ctx, status, cursor)
	}
	var after *time.Time
	if cursor != "" {
		value, err := time.Parse(time.RFC3339Nano, cursor)
		if err != nil {
			return forecastRunPage{}, err
		}
		after = &value
	}
	state := workflow.RunStatus(status)
	runs, next, err := h.queries.ListRuns(ctx, 100, after, &state)
	if err != nil {
		return forecastRunPage{}, err
	}
	page := forecastRunPage{Items: make([]forecastRun, 0, len(runs))}
	for _, run := range runs {
		page.Items = append(page.Items, forecastRun{
			RunID: run.ID.String(), IssueTime: run.IssueTime.UTC().Format(time.RFC3339Nano),
			GridID: run.GridID, Status: string(run.Status),
			CreatedAt: run.CreatedAt.UTC().Format(time.RFC3339Nano),
			// The existing ForecastRun API has no execution_mode field.
		})
	}
	if next != nil {
		text := next.UTC().Format(time.RFC3339Nano)
		page.NextCursor = &text
	}
	return page, nil
}

func (h *Handler) queryAnalysisPage(ctx context.Context, cursor string) (analysisCyclePage, error) {
	if h.queries == nil {
		return h.legacyAnalysisPage(ctx, cursor)
	}
	var after *uuid.UUID
	if cursor != "" {
		value, err := uuid.Parse(cursor)
		if err != nil {
			return analysisCyclePage{}, err
		}
		after = &value
	}
	status := workflow.AnalysisReady
	cycles, next, err := h.queries.ListAnalysisCyclesPage(ctx, 200, &status, after)
	if err != nil {
		return analysisCyclePage{}, err
	}
	page := analysisCyclePage{Items: make([]analysisCycle, 0, len(cycles))}
	for _, cycle := range cycles {
		value, err := domainAnalysisView(cycle)
		if err != nil {
			return analysisCyclePage{}, err
		}
		page.Items = append(page.Items, value)
	}
	if next != nil {
		text := next.String()
		page.NextCursor = &text
	}
	return page, nil
}

func (h *Handler) queryAnalysis(ctx context.Context, id string) (analysisCycle, error) {
	if h.queries == nil {
		return h.legacyAnalysis(ctx, id)
	}
	key, err := uuid.Parse(id)
	if err != nil {
		return analysisCycle{}, err
	}
	cycle, err := h.queries.GetAnalysisCycle(ctx, key)
	if err != nil {
		return analysisCycle{}, err
	}
	return domainAnalysisView(cycle)
}

func domainAnalysisView(c workflow.AnalysisCycle) (analysisCycle, error) {
	coverage, err := queryLegacyFloat32(c.ValidCoverageRatio)
	if err != nil {
		return analysisCycle{}, err
	}
	value := analysisCycle{
		AnalysisID: c.ID.String(), RunID: c.RunID.String(),
		AnalysisTime: c.AnalysisTime.UTC().Format(time.RFC3339Nano),
		GridID:       c.GridID, ConfigVersion: c.ConfigVersion, Status: string(c.Status),
		CreatedAt: c.CreatedAt.UTC().Format(time.RFC3339Nano),
		MosaicURI: queryCloneString(c.MosaicURI), AnalysisURI: queryCloneString(c.AnalysisURI),
		RadarCount: c.RadarCount, CoverageRatio: coverage,
		Radars: make([]analysisRadar, 0, len(c.Radars)),
		// Do not infer operational eligibility from ANALYSIS_READY.
	}
	if c.DegradedReason != nil {
		value.DegradedReason = *c.DegradedReason
	}
	for _, r := range c.Radars {
		quality, err := queryLegacyFloat32(r.MeanQualityIndex)
		if err != nil {
			return analysisCycle{}, err
		}
		radar := analysisRadar{RadarID: r.RadarID, State: string(r.State),
			MeanQualityIndex: quality, TimeOffsetSeconds: queryCloneInt(r.TimeOffsetSeconds)}
		if r.ScanID != nil {
			id := r.ScanID.String()
			radar.ScanID = &id
		}
		value.Radars = append(value.Radars, radar)
	}
	return value, nil
}

func (h *Handler) queryQPE(ctx context.Context, id string) (qpeSummary, error) {
	if h.queries == nil {
		return h.legacyQPE(ctx, id)
	}
	key, err := uuid.Parse(id)
	if err != nil {
		return qpeSummary{}, err
	}
	q, err := h.queries.GetAnalysisQPEMetrics(ctx, key)
	if err != nil {
		return qpeSummary{}, err
	}
	for _, n := range []float64{q.ValidCoverageRatio, q.MeanQualityIndex, q.MaximumObservedRateMMH, q.P95RateMMH} {
		if math.IsNaN(n) || math.IsInf(n, 0) {
			return qpeSummary{}, fmt.Errorf("invalid QPE summary number")
		}
	}
	return qpeSummary{
		AnalysisID: q.AnalysisID.String(), AnalysisTime: q.AnalysisTime.Format(time.RFC3339Nano),
		GridID: q.GridID, QPEConfigVersion: q.QPEConfigVersion,
		MosaicConfigVersion: q.MosaicConfigVersion, MosaicAlgorithmVersion: q.MosaicAlgorithmVersion,
		InputMosaicURI: q.InputMosaicURI, CoverageRatio: &q.ValidCoverageRatio,
		MeanQualityIndex: &q.MeanQualityIndex, MaximumRateMMH: &q.MaximumObservedRateMMH,
		P95RateMMH: &q.P95RateMMH,
	}, nil
}

func (h *Handler) queryDiagnostics(ctx context.Context, id string) (diagnosticBundle, error) {
	if h.queries == nil {
		return h.legacyDiagnostics(ctx, id)
	}
	key, err := uuid.Parse(id)
	if err != nil {
		return diagnosticBundle{}, err
	}
	d, err := h.queries.GetAnalysisDiagnostics(ctx, key)
	if err != nil {
		return diagnosticBundle{}, err
	}
	return domainDiagnosticView(d)
}

func domainDiagnosticView(d workflow.AnalysisDiagnostics) (diagnosticBundle, error) {
	result := diagnosticBundle{AnalysisTime: d.Manifest.AnalysisTime.UTC().Format(time.RFC3339Nano),
		Layers: make([]diagnosticLayer, 0, len(d.Manifest.Layers))}
	for _, layer := range d.Manifest.Layers {
		elevation, err := queryLegacyFloat32(layer.ElevationDeg)
		if err != nil {
			return diagnosticBundle{}, err
		}
		distance, err := queryLegacyFloat32(layer.MaximumRangeKM)
		if err != nil {
			return diagnosticBundle{}, err
		}
		value := diagnosticLayer{
			LayerID: layer.LayerID, Scope: layer.Scope, Field: layer.Field, Title: layer.Title,
			ImageURL: fmt.Sprintf("/api/v1/diagnostics/%s/layers/%s", d.JobID, layer.LayerID),
			Unit:     queryCloneString(layer.Unit), SweepNumber: queryCloneInt(layer.SweepNumber),
			ElevationDeg: elevation, MaximumRangeKM: distance,
			Legend: make([]diagnosticLegend, 0, len(layer.Legend)),
		}
		if layer.RadarID != nil {
			value.RadarID = *layer.RadarID
		}
		if layer.ScanID != nil {
			value.ScanID = layer.ScanID.String()
		}
		if len(layer.Bounds) == 4 {
			value.Bounds = make([]float64, 4)
			for i, n := range layer.Bounds {
				converted, err := queryLegacyFloat32(&n)
				if err != nil {
					return diagnosticBundle{}, err
				}
				value.Bounds[i] = *converted
			}
		}
		for _, legend := range layer.Legend {
			// Compatibility: the domain API emits "value", whereas the old
			// workspace subset decodes "minimum". Do NOT silently fix this
			// display contract in a behavior-preserving architectural change.
			value.Legend = append(value.Legend, diagnosticLegend{Label: legend.Label, Color: legend.Color})
		}
		result.Layers = append(result.Layers, value)
	}
	return result, nil
}

// Match the former float32 JSON -> float64 subset decoding exactly. A simple
// float64(float32(v)) changes displayed decimal digits (e.g. 0.1 -> 0.10000000149).
func queryLegacyFloat32(value *float64) (*float64, error) {
	if value == nil {
		return nil, nil
	}
	n := float32(*value)
	if math.IsNaN(float64(n)) || math.IsInf(float64(n), 0) {
		return nil, fmt.Errorf("non-finite workspace number")
	}
	text := strconv.FormatFloat(float64(n), 'g', -1, 32)
	converted, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return nil, err
	}
	return &converted, nil
}

func queryCloneString(p *string) *string {
	if p == nil {
		return nil
	}
	v := *p
	return &v
}
func queryCloneInt(p *int) *int {
	if p == nil {
		return nil
	}
	v := *p
	return &v
}

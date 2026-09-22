package workspace

import (
	"encoding/json"
	"math"
	"reflect"
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

func TestBatch3FloatViewMatchesLegacyJSON(t *testing.T) {
	for _, value := range []float64{0, .1, .123456789, 118.123456789, -1.7, 1e-30, 1e30} {
		got, err := queryLegacyFloat32(&value)
		if err != nil {
			t.Fatal(err)
		}
		raw, err := json.Marshal(float32(value))
		if err != nil {
			t.Fatal(err)
		}
		var want float64
		if err = json.Unmarshal(raw, &want); err != nil {
			t.Fatal(err)
		}
		if *got != want {
			t.Fatalf("%g: got %g want %g", value, *got, want)
		}
	}
	if v, e := queryLegacyFloat32(nil); v != nil || e != nil {
		t.Fatal(v, e)
	}
	for _, v := range []float64{math.NaN(), math.Inf(1), math.Inf(-1), 1e300} {
		if _, e := queryLegacyFloat32(&v); e == nil {
			t.Fatal("accepted non-finite float32")
		}
	}
}
func TestBatch3AnalysisViewPreservesMissingAndLineage(t *testing.T) {
	id := uuid.MustParse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
	at := time.Date(2026, 9, 22, 21, 0, 0, 123456789, time.FixedZone("local", 8*3600))
	q := .1
	uri := "s3://rainpulse/immutable/analysis"
	reason := "missing station"
	offset := -145
	c := workflow.AnalysisCycle{ID: id, RunID: id, AnalysisTime: at, CreatedAt: at, GridID: "grid", ConfigVersion: "c1", Status: workflow.AnalysisReady,
		ValidCoverageRatio: &q, AnalysisURI: &uri, RadarCount: 2, DegradedReason: &reason,
		Radars: []workflow.AnalysisRadar{{RadarID: "z1", State: workflow.AnalysisRadarParticipating, ScanID: &id, TimeOffsetSeconds: &offset, MeanQualityIndex: &q}, {RadarID: "z2", State: workflow.AnalysisRadarMissing}}}
	v, e := domainAnalysisView(c)
	if e != nil {
		t.Fatal(e)
	}
	if v.Operational || v.AnalysisID != id.String() || v.AnalysisTime != "2026-09-22T13:00:00.123456789Z" || *v.CoverageRatio != .1 {
		t.Fatal(v)
	}
	if v.MosaicURI != nil || v.Radars[1].ScanID != nil || v.Radars[1].MeanQualityIndex != nil || v.DegradedReason != reason {
		t.Fatal(v)
	}
	*v.AnalysisURI = "changed"
	*v.Radars[0].TimeOffsetSeconds = 999
	if uri != "s3://rainpulse/immutable/analysis" || offset != -145 {
		t.Fatal("view mutates domain pointers")
	}
}
func TestBatch3DiagnosticViewPreservesLegacyWireSubset(t *testing.T) {
	id := uuid.MustParse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
	at := time.Date(2026, 9, 22, 13, 0, 0, 0, time.UTC)
	radar := "z1"
	unit := "dBZ"
	elev := .5
	distance := 460.
	cut := 0
	value := 10.
	d := workflow.AnalysisDiagnostics{JobID: id, Manifest: workflow.DiagnosticManifest{AnalysisTime: at, Layers: []workflow.DiagnosticLayer{
		{LayerID: "ppi", Scope: "polar", Field: "DBZH_QC", Title: "回波", RadarID: &radar, ScanID: &id, Unit: &unit, SweepNumber: &cut, ElevationDeg: &elev, MaximumRangeKM: &distance,
			Bounds: []float64{118.1, 25.1, 123.1, 27.1}, Legend: []workflow.DiagnosticLegendEntry{{Label: "10", Color: "#abc", Value: &value}}},
		{LayerID: "grid", Bounds: []float64{1, 2, 3}},
	}}}
	got, err := domainDiagnosticView(d)
	if err != nil {
		t.Fatal(err)
	}
	// Frozen JSON subset of the old API decoder: "value" did not populate "minimum".
	var want diagnosticBundle
	raw := `{"analysis_time":"2026-09-22T13:00:00Z","layers":[{"layer_id":"ppi","scope":"polar","field":"DBZH_QC","title":"回波","radar_id":"z1","scan_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","unit":"dBZ","sweep_number":0,"elevation_deg":0.5,"maximum_range_km":460,"bounds":[118.1,25.1,123.1,27.1],"image_url":"/api/v1/diagnostics/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/layers/ppi","legend":[{"label":"10","color":"#abc","value":10}]},{"layer_id":"grid","image_url":"/api/v1/diagnostics/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/layers/grid","legend":[]}]}`
	if err = json.Unmarshal([]byte(raw), &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v\nwant %#v", got, want)
	}
}

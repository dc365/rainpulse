package multiband

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func fixture(t *testing.T) Network {
	t.Helper()
	raw := `{"schema_version":"1.0","release_id":"fixture-sx","stations":{
 "s1":{"band":"S","frequency_hz":2800000000,"longitude_deg":119.3,"latitude_deg":26.1,"altitude_m_msl":100,"beam_width_h_deg":1,"beam_width_v_deg":1,"source":"s_qc_zarr","enabled":true,"geometry_verified":true,"calibration_verified":true,"calibration_id":"fixture","nominal_cadence_seconds":360,"maximum_age_seconds":360,"quality_scale":1,"allowed_s_qc_versions":["s-v1"]},
 "x1":{"band":"X","frequency_hz":9400000000,"longitude_deg":119.3,"latitude_deg":26.1,"altitude_m_msl":100,"beam_width_h_deg":1,"beam_width_v_deg":1,"source":"native_bundle","enabled":true,"geometry_verified":true,"calibration_verified":true,"calibration_id":"fixture","nominal_cadence_seconds":60,"maximum_age_seconds":120,"quality_scale":1,"x_qc":{"attenuation":"none"}}},
 "products":{"local":{"grid_id":"g","crs":"EPSG:32651","west_m":1000,"south_m":1000,"spacing_m":500,"width":10,"height":10,"levels_m_msl":[500,1000]}}}`
	n, e := Parse([]byte(raw))
	if e != nil {
		t.Fatal(e)
	}
	return n
}
func clock() time.Time { return time.Date(2026, 9, 23, 0, 10, 0, 0, time.UTC) }
func scan(id, radar string, at time.Time) Scan {
	return Scan{ID: id, RadarID: radar, Start: at.Add(-10 * time.Second), End: at, AvailableAt: at.Add(time.Second), NormalizedURI: "s3://b/normalized/" + id, QCURI: "s3://b/qc/" + id}
}

func TestLatestCausalSelectionAndSReuse(t *testing.T) {
	n := fixture(t)
	at := clock()
	all := []Scan{scan("s-old", "s1", at.Add(-3*time.Minute)), scan("x-old", "x1", at.Add(-time.Minute)), scan("x-new", "x1", at), scan("x-future", "x1", at.Add(time.Minute))}
	out, w, e := Select(n, "local", "sx_composite", []string{"x1", "s1"}, at, at.Add(time.Minute), at.Add(10*time.Second), all)
	if e != nil || len(out) != 1 || len(out[0].Sources) != 2 || len(w) != 0 {
		t.Fatalf("%+v %+v %v", out, w, e)
	}
	if out[0].Sources[0].ScanID != "s-old" || out[0].Sources[1].ScanID != "x-new" {
		t.Fatal(out)
	}
	if !strings.Contains(out[0].Sources[0].InputURI, "/qc/") || !strings.Contains(out[0].Sources[1].InputURI, "/normalized/") {
		t.Fatal("wrong band source")
	}
	if !out[0].Sources[0].End.Equal(at.Add(-3 * time.Minute)) {
		t.Fatal("old observation was retimestamped")
	}
}
func TestExpiryAndUnavailableArrival(t *testing.T) {
	n := fixture(t)
	at := clock()
	x := scan("x", "x1", at)
	x.AvailableAt = at.Add(time.Hour)
	out, w, e := Select(n, "local", "sx_composite", []string{"s1", "x1"}, at, at.Add(time.Minute), at.Add(10*time.Second), []Scan{scan("s", "s1", at.Add(-7*time.Minute)), x})
	if e != nil || len(out) != 0 || len(w) != 2 {
		t.Fatalf("%+v %+v %v", out, w, e)
	}
}
func TestSOnlyFallbackAndActualXTiming(t *testing.T) {
	n := fixture(t)
	at := clock()
	out, w, e := Select(n, "local", "sx_composite", []string{"s1", "x1"}, at, at.Add(time.Minute), at.Add(10*time.Second), []Scan{scan("s", "s1", at.Add(-time.Minute))})
	if e != nil || len(out) != 1 || len(out[0].Sources) != 1 || len(w) != 1 {
		t.Fatal(out, w, e)
	}
	x := scan("x", "x1", at.Add(12*time.Second))
	out, _, e = Select(n, "local", "x_qc", []string{"x1"}, at, at.Add(time.Minute), at.Add(2*time.Minute), []Scan{x})
	if e != nil || len(out) != 1 || !out[0].AnalysisTime.Equal(at.Add(time.Minute)) || !out[0].Sources[0].End.Equal(x.End) {
		t.Fatal(out, e)
	}
}
func TestInputOrderIndependent(t *testing.T) {
	n := fixture(t)
	at := clock()
	a := scan("a", "x1", at)
	b := scan("b", "x1", at)
	out, _, e := Select(n, "local", "sx_composite", []string{"x1"}, at, at.Add(time.Minute), at.Add(time.Minute), []Scan{b, a})
	if e != nil || out[0].Sources[0].ScanID != "a" {
		t.Fatal(out, e)
	}
}
func TestBoundedPlanAndBandValidation(t *testing.T) {
	n := fixture(t)
	at := clock()
	for _, tc := range []struct {
		name, mode, product string
		radars              []string
		end                 time.Time
	}{
		{"too-long", "sx_composite", "local", []string{"x1"}, at.Add(2 * time.Hour)},
		{"duplicate", "sx_composite", "local", []string{"x1", "x1"}, at.Add(time.Minute)},
		{"wrong-band", "x_qc", "local", []string{"s1"}, at.Add(time.Minute)},
		{"unknown-product", "sx_composite", "bad", []string{"x1"}, at.Add(time.Minute)},
		{"invalid-mode", "bad", "local", []string{"x1"}, at.Add(time.Minute)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, _, e := Select(n, tc.product, tc.mode, tc.radars, at, tc.end, at.Add(3*time.Hour), nil); e == nil {
				t.Fatal("accepted invalid plan")
			}
		})
	}
	scans := make([]Scan, 4097)
	if _, _, e := Select(n, "local", "sx_composite", []string{"x1"}, at, at.Add(time.Minute), at.Add(time.Hour), scans); e == nil {
		t.Fatal("unbounded inventory")
	}
}
func TestMinuteSlotsStableAcrossRelease(t *testing.T) {
	at := clock()
	if Slot("a", at) == Slot("b", at) || Slot("a", at) == Slot("a", at.Add(time.Minute)) {
		t.Fatal("slot collision")
	}
	if len(Slot("a", at)) != 36 || Slot("a", at) != Slot("a", at.In(time.FixedZone("UTC8", 8*3600))) {
		t.Fatal("unstable identity")
	}
}
func TestNetworkExplicitGeometryAndBand(t *testing.T) {
	n := fixture(t)
	raw, _ := json.Marshal(n)
	for _, tc := range []struct{ name, from, to string }{
		{"frequency", "9400000000", "2800000000"},
		{"beam", `"beam_width_h_deg":1`, `"beam_width_h_deg":0`},
		{"geometry", `"geometry_verified":true`, `"geometry_verified":false`},
		{"source", `"source":"s_qc_zarr"`, `"source":"normalized_zarr"`},
		{"minute", `"cadence_seconds":60`, `"cadence_seconds":30`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := strings.ReplaceAll(string(raw), tc.from, tc.to)
			if _, e := Parse([]byte(b)); e == nil {
				t.Fatal("accepted invalid network")
			}
		})
	}
	if _, e := Parse(append(raw, []byte(" {}")...)); e == nil {
		t.Fatal("trailing JSON")
	}
	if _, e := Parse([]byte(strings.Repeat(" ", 1<<20+1))); e == nil {
		t.Fatal("oversized JSON")
	}
}

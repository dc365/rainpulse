package operations

import (
	"testing"
	"time"
)

func TestMultiBandSelectionsAndArtifactBoundary(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Minute)
	for _, preset := range []string{"x_qc", "sx_composite"} {
		v := Selection{Preset: preset, ProductID: "local", Start: now.Add(-time.Hour), End: now, RadarIDs: []string{"x1"}}
		if e := v.Validate(); e != nil {
			t.Fatal(e)
		}
		v.Start = v.Start.Add(-time.Minute)
		if e := v.Validate(); e == nil {
			t.Fatal("unbounded multiband request accepted")
		}
		v.Start = now.Add(-time.Minute)
		v.ProductID = "../invalid"
		if e := v.Validate(); e == nil {
			t.Fatal("unsafe product id accepted")
		}
	}
	i := identity("multiband")
	if e := i.Validate(); e != nil {
		t.Fatal(e)
	}
	if ArtifactName("multiband") != "multiband" {
		t.Fatal("candidate artifact is not isolated")
	}
}

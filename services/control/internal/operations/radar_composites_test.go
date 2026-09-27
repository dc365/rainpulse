package operations

import "testing"

func TestCompositeMapManifest(t *testing.T) {
	good := []byte(`{"contract":"rainpulse.multiband.composite-v1","comparison":{"same_grid":true,"products":[{"product_id":"s_only","map":{"crs":"EPSG:4326","bounds":[118,25,120,27],"object_path":"map/s_only.png"}}]}}`)
	if _, err := parseCompositeManifest(good); err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{
		`{"contract":"rainpulse.multiband.composite-v1","comparison":{"same_grid":false}}`,
		`{"contract":"rainpulse.multiband.composite-v1","comparison":{"same_grid":true,"products":[{"map":{"crs":"EPSG:32651","bounds":[118,25,120,27],"object_path":"map/s.png"}}]}}`,
		`{"contract":"rainpulse.multiband.composite-v1","comparison":{"same_grid":true,"products":[{"map":{"crs":"EPSG:4326","bounds":[120,25,118,27],"object_path":"map/s.png"}}]}}`,
		`{"contract":"rainpulse.multiband.composite-v1","comparison":{"same_grid":true,"products":[{"map":{"crs":"EPSG:4326","bounds":[118,25,120,27],"object_path":"../s.png"}}]}}`,
	} {
		if _, err := parseCompositeManifest([]byte(raw)); err == nil {
			t.Fatalf("accepted invalid map: %s", raw)
		}
	}
}

func TestCompositeExperimentMetadataPreserved(t *testing.T) {
	m, err := parseCompositeManifest([]byte(`{"contract":"rainpulse.multiband.composite-v1","experimental":true,"display_warning":"未标定试验","valid_echo_cells":12,"comparison":{"same_grid":true,"products":[{"product_id":"sx_composite","valid_echo_cells":12,"echo_contributing_bands":["S","X"]}]}}`))
	if err != nil || !m.Experimental || m.DisplayWarning == "" || m.ValidEchoCells != 12 || len(m.Comparison.Products[0].EchoContributingBands) != 2 {
		t.Fatalf("lost experiment metadata: %+v %v", m, err)
	}
}

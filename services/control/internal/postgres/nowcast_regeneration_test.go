package postgres

import "testing"

func TestRegenerationPresetFromReason(t *testing.T) {
	for _, preset := range []string{"forecast_all", "pysteps_lk", "products"} {
		value, ok := regenerationPresetFromReason(
			"manual-regeneration/" + preset + ": operator validation",
		)
		if !ok || value != preset {
			t.Fatalf("preset %q parsed as %q, ok=%v", preset, value, ok)
		}
	}
	for _, reason := range []string{
		"manual regeneration",
		"manual-regeneration/custom: unsafe",
		"manual-regeneration/products",
	} {
		if preset, ok := regenerationPresetFromReason(reason); ok {
			t.Fatalf("invalid reason %q parsed as %q", reason, preset)
		}
	}
}

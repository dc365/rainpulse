package postgres

import "testing"

func TestQPEPeakWithinStoragePrecisionAcceptsFloat32RoundTrip(t *testing.T) {
	if !qpePeakWithinStoragePrecision(86.46816701021315, 86.46817016601562) {
		t.Fatal("float32 product peak round-trip was rejected")
	}
}

func TestQPEPeakWithinStoragePrecisionRejectsMaterialOverrun(t *testing.T) {
	if qpePeakWithinStoragePrecision(86.46816701021315, 86.47) {
		t.Fatal("material product peak overrun was accepted")
	}
}

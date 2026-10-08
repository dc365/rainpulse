package postgres

import (
	"testing"
	"time"
)

func TestDiagnosticSupplementRequiresCausalNativeSQC(t *testing.T) {
	at := time.Date(2026, 8, 28, 0, 12, 0, 0, time.UTC)
	for _, tc := range []struct {
		name, band, uri, expected string
		end                       time.Time
		ok                        bool
	}{
		{"valid", "S", "s3://rainpulse/radar/qc/z9595/native/volume.zarr", "s3://rainpulse/radar/qc/z9595/native/volume.zarr", at.Add(-120 * time.Second), true},
		{"future", "S", "s3://rainpulse/a", "s3://rainpulse/a", at.Add(time.Second), false},
		{"stale", "S", "s3://rainpulse/a", "s3://rainpulse/a", at.Add(-721 * time.Second), false},
		{"not-S", "X", "s3://rainpulse/a", "s3://rainpulse/a", at, false},
		{"different-QC", "S", "s3://rainpulse/a", "s3://rainpulse/b", at, false},
		{"no-QC", "S", "", "", at, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := validDiagnosticSupplement(tc.band, tc.uri, tc.expected, tc.end, at); got != tc.ok {
				t.Fatalf("valid=%v want=%v", got, tc.ok)
			}
		})
	}
}

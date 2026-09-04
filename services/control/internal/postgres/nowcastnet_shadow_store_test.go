package postgres

import (
	"testing"
	"time"
)

func TestNowcastNetShadowInputTimesSelectsNineExactTenMinuteFrames(t *testing.T) {
	issue := time.Date(2026, 8, 28, 2, 25, 0, 0, time.FixedZone("CST", 8*60*60))
	values := nowcastNetShadowInputTimes(issue)
	if len(values) != 9 {
		t.Fatalf("input frame count = %d, want 9", len(values))
	}
	for index, value := range values {
		want := issue.UTC().Add(time.Duration(-80+10*index) * time.Minute)
		if !value.Equal(want) {
			t.Fatalf("input frame %d = %s, want %s", index, value, want)
		}
	}
}

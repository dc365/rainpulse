package postgres

import (
	"strings"
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

func TestNowcastNetShadowInputPrefersNewestAnalysisPerValidTime(t *testing.T) {
	for _, fragment := range []string{
		"DISTINCT ON (analysis_time)",
		"ORDER BY analysis_time, created_at DESC",
		"ORDER BY analysis_time",
	} {
		if !strings.Contains(nowcastNetShadowAnalysisFramesQuery, fragment) {
			t.Fatalf("NowcastNet input query does not contain %q", fragment)
		}
	}
}

package workspace

import (
	"testing"
	"time"
)

func TestTimelineIncludesMissingSlotsAndCrossesMidnight(t *testing.T) {
	d := cycleDetail{cycleSummary: cycleSummary{IssueTime: "2026-08-28T00:00:00Z"}}
	finalizeTimeline(&d)
	if len(d.Timeline) != 41 || d.Timeline[0] != "2026-08-27T23:00:00Z" || d.Timeline[10] != d.IssueTime || d.Timeline[40] != "2026-08-28T03:00:00Z" {
		t.Fatalf("unexpected timeline: %v", d.Timeline)
	}
	for i := 1; i < len(d.Timeline); i++ {
		before, _ := time.Parse(time.RFC3339, d.Timeline[i-1])
		after, _ := time.Parse(time.RFC3339, d.Timeline[i])
		if after.Sub(before) != 6*time.Minute {
			t.Fatal("non-uniform timeline")
		}
	}
	finalizeTimeline(&d)
	if len(d.Timeline) != 41 {
		t.Fatal("repeated projection duplicated times")
	}
}

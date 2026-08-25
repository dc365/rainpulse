package main

import (
	"testing"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
)

func TestRequestedSubjectCoversEveryRequestEvent(t *testing.T) {
	tests := []struct {
		eventType string
		subject   string
	}{
		{orchestration.JobRequestedEventType, orchestration.JobRequestedSubject},
		{orchestration.RadarDecodeRequestedEventType, orchestration.RadarDecodeRequestedSubject},
		{orchestration.RadarQCRequestedEventType, orchestration.RadarQCRequestedSubject},
		{orchestration.RadarGridRequestedEventType, orchestration.RadarGridRequestedSubject},
		{orchestration.AnalysisMosaicRequestedEventType, orchestration.AnalysisMosaicRequestedSubject},
		{orchestration.AnalysisQPERequestedEventType, orchestration.AnalysisQPERequestedSubject},
		{orchestration.AnalysisDiagnosticsRequestedEventType, orchestration.AnalysisDiagnosticsRequestedSubject},
		{orchestration.NowcastInputRequestedEventType, orchestration.NowcastInputRequestedSubject},
		{orchestration.PystepsLKRequestedEventType, orchestration.PystepsLKRequestedSubject},
		{orchestration.ProductBuildRequestedEventType, orchestration.ProductBuildRequestedSubject},
	}
	for _, test := range tests {
		t.Run(test.eventType, func(t *testing.T) {
			got, err := requestedSubject(test.eventType)
			if err != nil || got != test.subject {
				t.Fatalf("requestedSubject(%q) = %q, want %q", test.eventType, got, test.subject)
			}
		})
	}
	if _, err := requestedSubject("unknown.requested.v1"); err == nil {
		t.Fatal("unknown replay event type was routed instead of rejected")
	}
}

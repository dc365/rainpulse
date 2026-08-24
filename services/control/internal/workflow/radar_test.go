package workflow

import "testing"

func TestRadarScanStateMachineRejectsSkippedStages(t *testing.T) {
	if !CanTransitionRadarScan(RadarScanRawReceived, RadarScanRawValidating) {
		t.Fatal("expected RAW_RECEIVED -> RAW_VALIDATING")
	}
	if CanTransitionRadarScan(RadarScanRawReceived, RadarScanQCRunning) {
		t.Fatal("raw scan must not skip decode and normalization")
	}
	if !CanTransitionRadarScan(RadarScanQCRunning, RadarScanFailed) {
		t.Fatal("every non-terminal radar stage must be able to fail independently")
	}
	if CanTransitionRadarScan(RadarScanFailed, RadarScanGridReady) {
		t.Fatal("failed radar scan must remain terminal")
	}
}

func TestAnalysisStateMachineRequiresMosaicAndQPE(t *testing.T) {
	states := []AnalysisStatus{
		AnalysisOpen,
		AnalysisCollecting,
		AnalysisAligning,
		AnalysisMosaic,
		AnalysisQPE,
		AnalysisReady,
	}
	for index := 0; index < len(states)-1; index++ {
		if !CanTransitionAnalysis(states[index], states[index+1]) {
			t.Fatalf("expected %s -> %s", states[index], states[index+1])
		}
	}
	if CanTransitionAnalysis(AnalysisCollecting, AnalysisReady) {
		t.Fatal("analysis must not become ready before alignment, mosaic and QPE")
	}
}

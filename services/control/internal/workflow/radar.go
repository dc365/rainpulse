package workflow

import (
	"time"

	"github.com/google/uuid"
)

type WorkflowType string

const (
	WorkflowRadarScan     WorkflowType = "radar_scan"
	WorkflowAnalysisCycle WorkflowType = "analysis_cycle"
	WorkflowForecastRun   WorkflowType = "forecast_run"
)

type RadarLifecycle string

const (
	RadarDraft    RadarLifecycle = "draft"
	RadarReady    RadarLifecycle = "ready"
	RadarDisabled RadarLifecycle = "disabled"
)

type RadarHealthState string

const (
	RadarHealthUnknown     RadarHealthState = "UNKNOWN"
	RadarHealthHealthy     RadarHealthState = "HEALTHY"
	RadarHealthDegraded    RadarHealthState = "DEGRADED"
	RadarHealthUnavailable RadarHealthState = "UNAVAILABLE"
)

type RadarScanStatus string

const (
	RadarScanRawReceived   RadarScanStatus = "RAW_RECEIVED"
	RadarScanRawValidating RadarScanStatus = "RAW_VALIDATING"
	RadarScanDecoding      RadarScanStatus = "DECODING"
	RadarScanNormalized    RadarScanStatus = "NORMALIZED"
	RadarScanQCRunning     RadarScanStatus = "QC_RUNNING"
	RadarScanQCReady       RadarScanStatus = "QC_READY"
	RadarScanGridRunning   RadarScanStatus = "GRID_RUNNING"
	RadarScanGridReady     RadarScanStatus = "RADAR_GRID_READY"
	RadarScanDegraded      RadarScanStatus = "DEGRADED"
	RadarScanFailed        RadarScanStatus = "FAILED"
	RadarScanSkipped       RadarScanStatus = "SKIPPED"
)

type AnalysisStatus string

const (
	AnalysisOpen       AnalysisStatus = "OPEN"
	AnalysisCollecting AnalysisStatus = "COLLECTING_RADARS"
	AnalysisAligning   AnalysisStatus = "ALIGNING"
	AnalysisMosaic     AnalysisStatus = "MOSAIC_RUNNING"
	AnalysisQPE        AnalysisStatus = "QPE_RUNNING"
	AnalysisReady      AnalysisStatus = "ANALYSIS_READY"
	AnalysisDegraded   AnalysisStatus = "DEGRADED"
	AnalysisFailed     AnalysisStatus = "FAILED"
	AnalysisSkipped    AnalysisStatus = "SKIPPED"
)

type AnalysisRadarState string

const (
	AnalysisRadarParticipating AnalysisRadarState = "PARTICIPATING"
	AnalysisRadarMissing       AnalysisRadarState = "MISSING"
	AnalysisRadarFailed        AnalysisRadarState = "FAILED"
	AnalysisRadarExcluded      AnalysisRadarState = "EXCLUDED"
)

type Radar struct {
	ID            string
	DisplayName   *string
	Lifecycle     RadarLifecycle
	ConfigVersion string
	CreatedAt     time.Time
	UpdatedAt     time.Time
}

type RadarScan struct {
	ID                 uuid.UUID
	RunID              uuid.UUID
	RadarID            string
	VolumeStartTime    time.Time
	VolumeEndTime      time.Time
	ReceivedAt         time.Time
	RadarConfigVersion string
	Status             RadarScanStatus
	DegradedReason     *string
	NormalizedURI      *string
	QCURI              *string
	GridURI            *string
	ScanCompleteness   *float64
	MeanQualityIndex   *float64
	CreatedAt          time.Time
	UpdatedAt          time.Time
}

type RadarStatusSummary struct {
	RadarID                       string
	Health                        RadarHealthState
	LatestScanID                  *uuid.UUID
	LatestScanTime                *time.Time
	ScanStatus                    *RadarScanStatus
	ScanCompleteness              *float64
	MeanQualityIndex              *float64
	DataDelaySeconds              *int64
	ParticipatingInLatestAnalysis bool
}

type AnalysisRadar struct {
	RadarID           string
	ScanID            *uuid.UUID
	State             AnalysisRadarState
	TimeOffsetSeconds *int
	MeanQualityIndex  *float64
	ExclusionReason   *string
}

type AnalysisCycle struct {
	ID                 uuid.UUID
	RunID              uuid.UUID
	AnalysisTime       time.Time
	GridID             string
	ConfigVersion      string
	Status             AnalysisStatus
	DegradedReason     *string
	RadarCount         int
	ValidCoverageRatio *float64
	MeanQualityIndex   *float64
	AnalysisURI        *string
	Radars             []AnalysisRadar
	CreatedAt          time.Time
	UpdatedAt          time.Time
}

type DomainSimulation struct {
	Radars   []Radar
	Scans    []RadarScan
	Analysis AnalysisCycle
}

var radarScanTransitions = map[RadarScanStatus]RadarScanStatus{
	RadarScanRawReceived:   RadarScanRawValidating,
	RadarScanRawValidating: RadarScanDecoding,
	RadarScanDecoding:      RadarScanNormalized,
	RadarScanNormalized:    RadarScanQCRunning,
	RadarScanQCRunning:     RadarScanQCReady,
	RadarScanQCReady:       RadarScanGridRunning,
	RadarScanGridRunning:   RadarScanGridReady,
}

var analysisTransitions = map[AnalysisStatus]AnalysisStatus{
	AnalysisOpen:       AnalysisCollecting,
	AnalysisCollecting: AnalysisAligning,
	AnalysisAligning:   AnalysisMosaic,
	AnalysisMosaic:     AnalysisQPE,
	AnalysisQPE:        AnalysisReady,
}

func CanTransitionRadarScan(from, to RadarScanStatus) bool {
	if from == to {
		return true
	}
	if isTerminalRadarScan(from) {
		return false
	}
	if to == RadarScanDegraded || to == RadarScanFailed || to == RadarScanSkipped {
		return true
	}
	return radarScanTransitions[from] == to
}

func CanTransitionAnalysis(from, to AnalysisStatus) bool {
	if from == to {
		return true
	}
	if isTerminalAnalysis(from) {
		return false
	}
	if to == AnalysisDegraded || to == AnalysisFailed || to == AnalysisSkipped {
		return true
	}
	return analysisTransitions[from] == to
}

func isTerminalRadarScan(status RadarScanStatus) bool {
	return status == RadarScanGridReady || status == RadarScanDegraded ||
		status == RadarScanFailed || status == RadarScanSkipped
}

func isTerminalAnalysis(status AnalysisStatus) bool {
	return status == AnalysisReady || status == AnalysisDegraded ||
		status == AnalysisFailed || status == AnalysisSkipped
}

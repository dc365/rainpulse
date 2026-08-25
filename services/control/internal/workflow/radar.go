package workflow

import (
	"encoding/json"
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
	DisplayName                   *string
	Lifecycle                     RadarLifecycle
	ConfigVersion                 string
	Health                        RadarHealthState
	LatestScanID                  *uuid.UUID
	LatestScanTime                *time.Time
	ScanStatus                    *RadarScanStatus
	ScanCompleteness              *float64
	MeanQualityIndex              *float64
	DataDelaySeconds              *int64
	ParticipatingInLatestAnalysis bool
	HealthMetrics                 *RadarHealthMetrics
	QCMetrics                     *RadarQCMetrics
}

type RadarFieldAvailability struct {
	Field               string  `json:"field"`
	Available           bool    `json:"available"`
	PresentSweepCount   int     `json:"present_sweep_count"`
	FiniteGateRatio     float64 `json:"finite_gate_ratio"`
	OutOfRangeGateCount int64   `json:"out_of_range_gate_count"`
	Unit                string  `json:"unit"`
}

type RadarNoiseLevel struct {
	Source        string   `json:"source"`
	HorizontalDBM *float64 `json:"horizontal_dbm"`
	VerticalDBM   *float64 `json:"vertical_dbm"`
	SampleCount   int      `json:"sample_count"`
}

type RadarHealthMetrics struct {
	ScanID                 uuid.UUID                `json:"scan_id"`
	RadarID                string                   `json:"radar_id"`
	RadarConfigVersion     string                   `json:"radar_config_version"`
	HealthProfileVersion   string                   `json:"health_profile_version"`
	Health                 RadarHealthState         `json:"health"`
	HealthReasons          []string                 `json:"health_reasons"`
	ScanCompleteness       float64                  `json:"scan_completeness"`
	ExpectedSweepCount     int                      `json:"expected_sweep_count"`
	ActualSweepCount       int                      `json:"actual_sweep_count"`
	MissingSweepNumbers    []int16                  `json:"missing_sweep_numbers"`
	ExpectedRadialCount    int                      `json:"expected_radial_count"`
	ActualRadialCount      int                      `json:"actual_radial_count"`
	MissingRadialCount     int                      `json:"missing_radial_count"`
	MaximumAzimuthGapDeg   float64                  `json:"maximum_azimuth_gap_deg"`
	FieldAvailabilityRatio float64                  `json:"field_availability_ratio"`
	FieldAvailability      []RadarFieldAvailability `json:"field_availability"`
	NoiseLevel             RadarNoiseLevel          `json:"noise_level"`
	ChannelStatus          string                   `json:"channel_status"`
	OutOfRangeGateCount    int64                    `json:"out_of_range_gate_count"`
	OutOfRangeGateRatio    float64                  `json:"out_of_range_gate_ratio"`
	AnomalyCount           int64                    `json:"anomaly_count"`
	LayerAnomalies         []map[string]any         `json:"layer_anomalies"`
	Warnings               []string                 `json:"warnings"`
	MeasuredAt             time.Time                `json:"measured_at"`
}

type RawRadarAsset struct {
	ID         uuid.UUID
	SourceID   uuid.UUID
	ObservedAt time.Time
	ObjectURI  string
	MediaType  string
	SizeBytes  int64
	SHA256     string
	Metadata   json.RawMessage
}

type RadarDecodeBundle struct {
	Radar        Radar
	Config       json.RawMessage
	ConfigSHA256 string
	Asset        RawRadarAsset
	Scan         RadarScan
	Job          Job
	Outbox       OutboxEvent
}

type RadarQCBundle struct {
	ScanID       uuid.UUID
	Status       RadarScanStatus
	Config       json.RawMessage
	ConfigSHA256 string
	Job          Job
	Outbox       OutboxEvent
}

type RadarGridBundle struct {
	ScanID       uuid.UUID
	Status       RadarScanStatus
	Config       json.RawMessage
	ConfigSHA256 string
	Job          Job
	Outbox       OutboxEvent
}

type AnalysisMosaicBundle struct {
	Analysis         AnalysisCycle
	AlgorithmVersion string
	Config           json.RawMessage
	ConfigSHA256     string
	Job              Job
	Outbox           OutboxEvent
}

type AnalysisQPEBundle struct {
	AnalysisID       uuid.UUID
	RunID            uuid.UUID
	CurrentStatus    AnalysisStatus
	MosaicURI        string
	ConfigVersion    string
	AlgorithmVersion string
	Config           json.RawMessage
	ConfigSHA256     string
	Job              Job
	Outbox           OutboxEvent
}

type AnalysisDiagnosticRadarInput struct {
	RadarID string    `json:"radar_id"`
	ScanID  uuid.UUID `json:"scan_id"`
	QCURI   string    `json:"qc_uri"`
}

type AnalysisDiagnosticsBundle struct {
	AnalysisID      uuid.UUID
	RunID           uuid.UUID
	AnalysisURI     string
	ConfigVersion   string
	RendererVersion string
	Config          json.RawMessage
	ConfigSHA256    string
	RadarInputs     []AnalysisDiagnosticRadarInput
	Job             Job
	Outbox          OutboxEvent
}

type NowcastInputFrame struct {
	AnalysisID         uuid.UUID `json:"analysis_id"`
	AnalysisTime       time.Time `json:"analysis_time"`
	InputURI           string    `json:"input_uri"`
	ValidCoverageRatio float64   `json:"valid_coverage_ratio"`
	MeanQualityIndex   float64   `json:"mean_quality_index"`
}

type NowcastInputBundle struct {
	Run               Run
	Frames            []NowcastInputFrame
	PreprocessVersion string
	GateConfigVersion string
	Config            json.RawMessage
	ConfigSHA256      string
	Job               Job
	Outbox            OutboxEvent
}

type NowcastInputMetrics struct {
	SchemaVersion       string      `json:"schema_version"`
	IssueTimeUTC        time.Time   `json:"issue_time_utc"`
	GridID              string      `json:"grid_id"`
	ProfileVersion      string      `json:"profile_version"`
	PreprocessVersion   string      `json:"preprocess_version"`
	AnalysisIDs         []uuid.UUID `json:"analysis_ids"`
	InputAssetIDs       []uuid.UUID `json:"input_asset_ids"`
	InputURIs           []string    `json:"input_uris"`
	FrameCount          int         `json:"frame_count"`
	TimestepMinutes     int         `json:"timestep_minutes"`
	ValidCoverageRatio  float64     `json:"valid_coverage_ratio"`
	MeanQualityIndex    float64     `json:"mean_quality_index"`
	MaxDataAgeMinutes   float64     `json:"max_data_age_minutes"`
	ValidCellCount      int64       `json:"valid_cell_count"`
	MissingCellCount    int64       `json:"missing_cell_count"`
	LowQualityCellCount int64       `json:"low_quality_cell_count"`
	OperationalEligible bool        `json:"operational_eligible"`
	OperationalReasons  []string    `json:"operational_reasons"`
	MeasuredAt          time.Time   `json:"measured_at"`
}

type PystepsLKBundle struct {
	Run             Run
	NowcastInputJob uuid.UUID
	InputURI        string
	InputAssetIDs   []uuid.UUID
	ModelRunID      uuid.UUID
	Config          json.RawMessage
	ConfigSHA256    string
	Job             Job
	Outbox          OutboxEvent
}

type PystepsLKMetrics struct {
	SchemaVersion                   string      `json:"schema_version"`
	RunID                           uuid.UUID   `json:"run_id"`
	JobID                           uuid.UUID   `json:"job_id"`
	IssueTime                       time.Time   `json:"issue_time"`
	GridID                          string      `json:"grid_id"`
	ModelID                         string      `json:"model_id"`
	ModelVersion                    string      `json:"model_version"`
	ConfigVersion                   string      `json:"config_version"`
	InputURI                        string      `json:"input_uri"`
	InputAssetIDs                   []uuid.UUID `json:"input_asset_ids"`
	LeadCount                       int         `json:"lead_count"`
	LeadStepMinutes                 int         `json:"lead_step_minutes"`
	ValidFrom                       time.Time   `json:"valid_from"`
	ValidTo                         time.Time   `json:"valid_to"`
	MotionFallbackUsed              bool        `json:"motion_fallback_used"`
	TrackableRainPixelCount         int64       `json:"trackable_rain_pixel_count"`
	FirstLeadValidCoverageRatio     float64     `json:"first_lead_valid_coverage_ratio"`
	LastLeadValidCoverageRatio      float64     `json:"last_lead_valid_coverage_ratio"`
	MaximumForecastRateMMH          float64     `json:"maximum_forecast_rate_mm_h"`
	BaselineModels                  []string    `json:"baseline_models"`
	MissingPolicy                   string      `json:"missing_policy"`
	RuntimeMS                       int64       `json:"runtime_ms"`
	GlobalTranslationXPixelsPerStep float64     `json:"global_translation_x_pixels_per_step"`
	GlobalTranslationYPixelsPerStep float64     `json:"global_translation_y_pixels_per_step"`
	MeanMotionUMS                   float64     `json:"mean_motion_u_m_s"`
	MeanMotionVMS                   float64     `json:"mean_motion_v_m_s"`
	MeasuredAt                      time.Time   `json:"measured_at"`
}

type DiagnosticLegendEntry struct {
	Label string   `json:"label"`
	Color string   `json:"color"`
	Value *float64 `json:"value,omitempty"`
	Code  *int     `json:"code,omitempty"`
}

type DiagnosticLayer struct {
	LayerID        string                  `json:"layer_id"`
	Title          string                  `json:"title"`
	Scope          string                  `json:"scope"`
	Field          string                  `json:"field"`
	Rendering      string                  `json:"rendering"`
	Unit           *string                 `json:"unit"`
	ObjectPath     string                  `json:"object_path"`
	Width          int                     `json:"width"`
	Height         int                     `json:"height"`
	PaletteVersion string                  `json:"palette_version"`
	Legend         []DiagnosticLegendEntry `json:"legend"`
	Bounds         []float64               `json:"bounds,omitempty"`
	RadarID        *string                 `json:"radar_id,omitempty"`
	ScanID         *uuid.UUID              `json:"scan_id,omitempty"`
	SweepNumber    *int                    `json:"sweep_number,omitempty"`
	ElevationDeg   *float64                `json:"elevation_deg,omitempty"`
	MaximumRangeKM *float64                `json:"maximum_range_km,omitempty"`
}

type DiagnosticManifest struct {
	ContractVersion       string            `json:"contract_version"`
	JobID                 uuid.UUID         `json:"job_id"`
	AnalysisID            uuid.UUID         `json:"analysis_id"`
	AnalysisTime          time.Time         `json:"analysis_time"`
	AnalysisURI           string            `json:"analysis_uri"`
	GridID                string            `json:"grid_id"`
	DiagnosticConfig      string            `json:"diagnostic_config_version"`
	RendererVersion       string            `json:"renderer_version"`
	PaletteVersion        string            `json:"palette_version"`
	FlagDefinitionVersion string            `json:"flag_definition_version"`
	OperationalEligible   bool              `json:"operational_eligible"`
	OperationalReasons    []string          `json:"operational_reasons"`
	Layers                []DiagnosticLayer `json:"layers"`
	CreatedAt             time.Time         `json:"created_at"`
}

type AnalysisDiagnostics struct {
	JobID     uuid.UUID
	BundleURI string
	Manifest  DiagnosticManifest
}

type RadarGridMetrics struct {
	ScanID                      uuid.UUID         `json:"scan_id"`
	RadarID                     string            `json:"radar_id"`
	GridID                      string            `json:"grid_id"`
	GridConfigVersion           string            `json:"grid_config_version"`
	ProfileVersion              string            `json:"profile_version"`
	AlgorithmVersion            string            `json:"algorithm_version"`
	DEMAssetVersion             string            `json:"dem_asset_version"`
	VerticalDatumStatus         string            `json:"vertical_datum_status"`
	OperationalEligible         bool              `json:"operational_eligible"`
	OperationalReasons          []string          `json:"operational_reasons"`
	GridCellCount               int64             `json:"grid_cell_count"`
	ValidCellCount              int64             `json:"valid_cell_count"`
	MissingCellCount            int64             `json:"missing_cell_count"`
	LowQualityCellCount         int64             `json:"low_quality_cell_count"`
	ValidCoverageRatio          float64           `json:"valid_coverage_ratio"`
	MeanQualityIndex            float64           `json:"mean_quality_index"`
	BeamBlockedMissingCellCount int64             `json:"beam_blocked_missing_cell_count"`
	SelectionCounts             map[string]int64  `json:"selection_counts"`
	SkippedSweeps               map[string]string `json:"skipped_sweeps"`
	MeasuredAt                  time.Time         `json:"measured_at"`
}

type AnalysisMosaicContributor struct {
	RadarID                  string    `json:"radar_id"`
	ScanID                   uuid.UUID `json:"scan_id"`
	GridURI                  string    `json:"grid_uri"`
	TimeOffsetSeconds        int       `json:"time_offset_seconds"`
	HybridScanVersion        string    `json:"hybrid_scan_version"`
	InputOperationalEligible bool      `json:"input_operational_eligible"`
	ContributingCellCount    int64     `json:"contributing_cell_count"`
	MeanAdjustedQualityIndex float64   `json:"mean_adjusted_quality_index"`
}

type AnalysisMosaicMetrics struct {
	AnalysisTime                 time.Time                   `json:"analysis_time"`
	GridID                       string                      `json:"grid_id"`
	GridConfigVersion            string                      `json:"grid_config_version"`
	ProfileVersion               string                      `json:"profile_version"`
	AlgorithmVersion             string                      `json:"algorithm_version"`
	OperationalEligible          bool                        `json:"operational_eligible"`
	OperationalReasons           []string                    `json:"operational_reasons"`
	InputRadarCount              int                         `json:"input_radar_count"`
	ActualContributingRadarCount int                         `json:"actual_contributing_radar_count"`
	GridCellCount                int64                       `json:"grid_cell_count"`
	ValidCellCount               int64                       `json:"valid_cell_count"`
	MissingCellCount             int64                       `json:"missing_cell_count"`
	LowQualityCellCount          int64                       `json:"low_quality_cell_count"`
	BlendedCellCount             int64                       `json:"blended_cell_count"`
	ValidCoverageRatio           float64                     `json:"valid_coverage_ratio"`
	MeanQualityIndex             float64                     `json:"mean_quality_index"`
	Contributors                 []AnalysisMosaicContributor `json:"contributors"`
	MeasuredAt                   time.Time                   `json:"measured_at"`
}

type AnalysisQPEMetrics struct {
	AnalysisID             uuid.UUID `json:"analysis_id"`
	AnalysisTime           time.Time `json:"analysis_time"`
	GridID                 string    `json:"grid_id"`
	GridConfigVersion      string    `json:"grid_config_version"`
	QPEConfigVersion       string    `json:"qpe_config_version"`
	QPEAlgorithmVersion    string    `json:"qpe_algorithm_version"`
	MosaicConfigVersion    string    `json:"mosaic_config_version"`
	MosaicAlgorithmVersion string    `json:"mosaic_algorithm_version"`
	FlagDefinitionVersion  string    `json:"flag_definition_version"`
	InputMosaicURI         string    `json:"input_mosaic_uri"`
	InputField             string    `json:"input_field"`
	CoefficientA           float64   `json:"coefficient_a"`
	ExponentB              float64   `json:"exponent_b"`
	NoRainBelowDBZ         float64   `json:"no_rain_below_dbz"`
	MaximumRateMMH         float64   `json:"maximum_rate_mm_h"`
	GaugeAdjustmentEnabled bool      `json:"gauge_adjustment_enabled"`
	OperationalEligible    bool      `json:"operational_eligible"`
	OperationalReasons     []string  `json:"operational_reasons"`
	GridCellCount          int64     `json:"grid_cell_count"`
	ValidCellCount         int64     `json:"valid_cell_count"`
	MissingCellCount       int64     `json:"missing_cell_count"`
	LowQualityCellCount    int64     `json:"low_quality_cell_count"`
	NoRainCellCount        int64     `json:"no_rain_cell_count"`
	RainCellCount          int64     `json:"rain_cell_count"`
	CappedCellCount        int64     `json:"capped_cell_count"`
	ValidCoverageRatio     float64   `json:"valid_coverage_ratio"`
	MeanQualityIndex       float64   `json:"mean_quality_index"`
	MeanRateMMH            float64   `json:"mean_rate_mm_h"`
	MaximumObservedRateMMH float64   `json:"maximum_observed_rate_mm_h"`
	UncappedMaximumRateMMH float64   `json:"uncapped_max_rate_mm_h"`
	P95RateMMH             float64   `json:"p95_rate_mm_h"`
	MeasuredAt             time.Time `json:"measured_at"`
}

type RadarQCMetrics struct {
	ScanID                     uuid.UUID         `json:"scan_id"`
	RadarID                    string            `json:"radar_id"`
	QCProfile                  string            `json:"qc_profile"`
	QCPipelineVersion          string            `json:"qc_pipeline_version"`
	FlagDefinitionVersion      string            `json:"flag_definition_version"`
	HealthState                RadarHealthState  `json:"health_state"`
	MeanQualityIndex           float64           `json:"mean_quality_index"`
	ValidGateCount             int64             `json:"valid_gate_count"`
	MissingGateCount           int64             `json:"missing_gate_count"`
	LowQualityGateCount        int64             `json:"low_quality_gate_count"`
	NoRainGateCount            int64             `json:"no_rain_gate_count"`
	RadialInterferenceRayCount int64             `json:"radial_interference_ray_count"`
	GroundClutterGateCount     int64             `json:"ground_clutter_gate_count"`
	SeaClutterGateCount        int64             `json:"sea_clutter_gate_count"`
	APGateCount                int64             `json:"ap_gate_count"`
	ModuleStatuses             map[string]string `json:"module_statuses"`
	MeasuredAt                 time.Time         `json:"measured_at"`
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
	MosaicURI          *string
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

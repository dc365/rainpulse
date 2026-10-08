package workspace

import "time"

// DataflowSnapshot is the read-only wall-clock projection behind the realtime
// dataflow screen. It answers, for one window: what already finished, what is
// running right now, and how each radar's quality-control chain behaved. The
// bounded domain tables remain authoritative; this payload carries only
// identities, states and timestamps.
type DataflowSnapshot struct {
	SchemaVersion  string                  `json:"schema_version"`
	GeneratedAt    time.Time               `json:"generated_at"`
	WindowStart    time.Time               `json:"window_start"`
	WindowMinutes  int                     `json:"window_minutes"`
	Stages         []DataflowStageSummary  `json:"stages"`
	RadarLanes     []DataflowRadarLane     `json:"radar_lanes"`
	AnalysisBlocks []DataflowAnalysisBlock `json:"analysis_blocks"`
	ForecastBlocks []DataflowForecastBlock `json:"forecast_blocks"`
	RadarStatuses  []DataflowRadarStatus   `json:"radar_statuses"`
	Events         []DataflowEvent         `json:"events"`
	Warnings       []string                `json:"warnings,omitempty"`
}

// DataflowStageSummary is one node of the chain beat strip. Key is one of the
// seven downstream groups; the ingest node counts arrived volume scans instead
// of jobs.
type DataflowStageSummary struct {
	Key       string `json:"key"`
	Label     string `json:"label"`
	Completed int    `json:"completed"`
	Running   int    `json:"running"`
	Queued    int    `json:"queued"`
	Failed    int    `json:"failed"`
	P50MS     int64  `json:"p50_ms"`
}

type DataflowRadarLane struct {
	RadarID string              `json:"radar_id"`
	Blocks  []DataflowScanBlock `json:"blocks"`
}

// DataflowScanBlock is one volume scan on the wall-clock lane. Stages keep the
// domain job identity so the drawer can stay honest about queue time versus
// compute time.
type DataflowScanBlock struct {
	ScanID           string             `json:"scan_id"`
	RunID            string             `json:"run_id"`
	RadarID          string             `json:"-"`
	VolumeStart      time.Time          `json:"volume_start"`
	VolumeEnd        time.Time          `json:"volume_end"`
	ReceivedAt       time.Time          `json:"received_at"`
	Status           string             `json:"status"`
	DegradedReason   string             `json:"degraded_reason,omitempty"`
	ScanCompleteness *float64           `json:"scan_completeness,omitempty"`
	MeanQualityIndex *float64           `json:"mean_quality_index,omitempty"`
	Stages           []DataflowJobStage `json:"stages"`
}

// DataflowJobStage mirrors one automatic-lane job row. Status keeps the
// workflow job enum; timestamps come from the jobs table, never the worker
// clock.
type DataflowJobStage struct {
	Stage        string     `json:"stage"`
	JobID        string     `json:"job_id"`
	Status       string     `json:"status"`
	CreatedAt    time.Time  `json:"created_at"`
	StartedAt    *time.Time `json:"started_at,omitempty"`
	FinishedAt   *time.Time `json:"finished_at,omitempty"`
	RuntimeMS    *int64     `json:"runtime_ms,omitempty"`
	ErrorCode    string     `json:"error_code,omitempty"`
	ErrorMessage string     `json:"error_message,omitempty"`
	ModelID      string     `json:"model_id,omitempty"`
}

type DataflowAnalysisBlock struct {
	AnalysisID     string             `json:"analysis_id"`
	RunID          string             `json:"run_id"`
	AnalysisTime   time.Time          `json:"analysis_time"`
	GridID         string             `json:"grid_id"`
	Status         string             `json:"status"`
	DegradedReason string             `json:"degraded_reason,omitempty"`
	RadarCount     int                `json:"radar_count"`
	CoverageRatio  *float64           `json:"coverage_ratio,omitempty"`
	CreatedAt      time.Time          `json:"created_at"`
	Stages         []DataflowJobStage `json:"stages"`
}

type DataflowForecastBlock struct {
	RunID      string             `json:"run_id"`
	IssueTime  time.Time          `json:"issue_time"`
	GridID     string             `json:"grid_id"`
	Status     string             `json:"status"`
	CreatedAt  time.Time          `json:"created_at"`
	UpdatedAt  *time.Time         `json:"updated_at,omitempty"`
	Stages     []DataflowJobStage `json:"stages"`
}

// DataflowRadarStatus is the per-radar current snapshot for the status strip,
// derived from the existing radar status summary plus the latest succeeded QC
// job runtime inside the window.
type DataflowRadarStatus struct {
	RadarID                       string     `json:"radar_id"`
	DisplayName                   string     `json:"display_name,omitempty"`
	Health                        string     `json:"health"`
	LatestScanTime                *time.Time `json:"latest_scan_time,omitempty"`
	ScanStatus                    string     `json:"scan_status,omitempty"`
	ScanCompleteness              *float64   `json:"scan_completeness,omitempty"`
	MeanQualityIndex              *float64   `json:"mean_quality_index,omitempty"`
	DataDelaySeconds              *int64     `json:"data_delay_seconds,omitempty"`
	ParticipatingInLatestAnalysis bool       `json:"participating_in_latest_analysis"`
	QCDurationMS                  *int64     `json:"qc_duration_ms,omitempty"`
}

// DataflowEvent is one terminal pipeline moment for the live ticker. Kinds are
// derived server-side from the same window rows; no bus event is fabricated.
type DataflowEvent struct {
	Time    time.Time `json:"time"`
	Kind    string    `json:"kind"`
	Label   string    `json:"label"`
	RadarID string    `json:"radar_id,omitempty"`
	Detail  string    `json:"detail,omitempty"`
}

// DataflowStripOrder is the fixed downstream order of the chain beat strip.
var DataflowStripOrder = []struct{ Key, Label string }{
	{"ingest", "数据到达"},
	{"decode", "解码"},
	{"qc", "极坐标质控"},
	{"grid", "格点化"},
	{"mosaic_qpe", "拼图 · QPE"},
	{"nowcast", "临近预报"},
	{"products", "产品发布"},
}

// dataflowStripGroup maps a domain job stage identity onto its chain beat
// group. An unmapped stage stays out of the strip but keeps its lane segment.
func dataflowStripGroup(stage string) string {
	switch stage {
	case "decode":
		return "decode"
	case "qc":
		return "qc"
	case "grid":
		return "grid"
	case "mosaic", "qpe", "diagnostics":
		return "mosaic_qpe"
	case "nowcast_input", "pysteps_lk", "pysteps_steps", "nowcastnet":
		return "nowcast"
	case "products", "verification":
		return "products"
	}
	return ""
}

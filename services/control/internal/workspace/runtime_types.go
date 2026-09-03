package workspace

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

var ErrProjectionNotFound = errors.New("workspace projection was not found")

type ProjectionRecord struct {
	Key         string
	StatusCode  int
	Header      HeaderValues
	Body        []byte
	ETag        string
	ExpiresAt   time.Time
	StaleUntil  time.Time
	GeneratedAt time.Time
}

// HeaderValues is kept as a small JSON-friendly representation so the persistent
// projection does not expose net/http types to PostgreSQL implementations.
type HeaderValues map[string][]string

type ProjectionStore interface {
	LoadWorkspaceProjection(context.Context, string) (ProjectionRecord, error)
	SaveWorkspaceProjection(context.Context, ProjectionRecord) error
	DeleteExpiredWorkspaceProjections(context.Context, time.Time, int) error
}

type RuntimeStore interface {
	GetAnalysisDiagnosticsByJob(context.Context, uuid.UUID) (workflow.AnalysisDiagnostics, error)
	GetProduct(context.Context, uuid.UUID) (workflow.Product, error)
	GetProductAsset(context.Context, uuid.UUID, uuid.UUID) (workflow.ProductAsset, error)
	ListProductAssets(context.Context, uuid.UUID) ([]workflow.ProductAsset, error)
	WorkspacePipelineSnapshot(context.Context, string, time.Time) (PipelineSnapshot, error)
	CancelWorkspaceRegeneration(context.Context, uuid.UUID, string) (RegenerationCancellation, error)
}

type RuntimeObjectReader interface {
	Read(context.Context, string, string) ([]byte, string, error)
	ReadObject(context.Context, string, int64) ([]byte, string, error)
	ReadRange(context.Context, string, int64, int64) ([]byte, int64, string, error)
}

type PipelineStage struct {
	StageID       string     `json:"stage_id"`
	Stage         string     `json:"stage"`
	DisplayName   string     `json:"display_name"`
	Status        string     `json:"status"`
	RunID         *uuid.UUID `json:"run_id,omitempty"`
	JobID         *uuid.UUID `json:"job_id,omitempty"`
	RadarID       string     `json:"radar_id,omitempty"`
	ModelID       string     `json:"model_id,omitempty"`
	ModelVersion  string     `json:"model_version,omitempty"`
	ConfigVersion string     `json:"config_version,omitempty"`
	QueueMS       *int64     `json:"queue_ms,omitempty"`
	RuntimeMS     *int64     `json:"runtime_ms,omitempty"`
	Attempt       int        `json:"attempt,omitempty"`
	StartedAt     *time.Time `json:"started_at,omitempty"`
	FinishedAt    *time.Time `json:"finished_at,omitempty"`
	ErrorCode     string     `json:"error_code,omitempty"`
	ErrorMessage  string     `json:"error_message,omitempty"`
}

type ActiveRegeneration struct {
	RequestID uuid.UUID `json:"request_id"`
	TargetRun uuid.UUID `json:"target_run_id"`
	Status    string    `json:"status"`
	Reason    string    `json:"reason"`
	CreatedAt time.Time `json:"created_at"`
}

type PipelineSnapshot struct {
	SchemaVersion      string              `json:"schema_version"`
	CycleID            string              `json:"cycle_id"`
	IssueTime          time.Time           `json:"issue_time"`
	GridID             string              `json:"grid_id"`
	GeneratedAt        time.Time           `json:"generated_at"`
	Stages             []PipelineStage     `json:"stages"`
	ActiveRegeneration *ActiveRegeneration `json:"active_regeneration,omitempty"`
}

type RegenerationCancellation struct {
	RequestID   uuid.UUID `json:"request_id"`
	TargetRunID uuid.UUID `json:"target_run_id"`
	Status      string    `json:"status"`
	Reason      string    `json:"reason"`
	CancelledAt time.Time `json:"cancelled_at"`
}

type ExactSample struct {
	SchemaVersion   string   `json:"schema_version"`
	AssetURL        string   `json:"asset_url"`
	Longitude       float64  `json:"longitude"`
	Latitude        float64  `json:"latitude"`
	GridLongitude   float64  `json:"grid_longitude"`
	GridLatitude    float64  `json:"grid_latitude"`
	Value           *float32 `json:"value,omitempty"`
	Confidence      *float32 `json:"confidence,omitempty"`
	Valid           bool     `json:"valid"`
	Unit            string   `json:"unit"`
	LeadTimeMinutes int      `json:"lead_time_minutes"`
	ValidTime       string   `json:"valid_time,omitempty"`
	FrameKind       string   `json:"frame_kind"`
	Derivation      string   `json:"derivation,omitempty"`
	Source          string   `json:"source"`
}

type pointQueryMetadata struct {
	ObjectPath  string   `json:"object_path"`
	SHA256      string   `json:"sha256"`
	SizeBytes   int64    `json:"size_bytes"`
	Unit        string   `json:"unit"`
	LeadMinutes []int    `json:"lead_minutes"`
	ValidTimes  []string `json:"valid_times"`
	FrameKinds  []string `json:"frame_kinds"`
	Derivations []string `json:"derivations,omitempty"`
	QualityKind string   `json:"quality_kind,omitempty"`
}

type fileProductManifest struct {
	BundleID     string                        `json:"bundle_id"`
	IssueTime    string                        `json:"issue_time"`
	GridID       string                        `json:"grid_id"`
	PointQueries map[string]pointQueryMetadata `json:"point_queries"`
}

func encodeHeader(value HeaderValues) json.RawMessage {
	data, _ := json.Marshal(value)
	return data
}

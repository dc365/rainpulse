package orchestration

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"regexp"
	"time"

	"github.com/google/uuid"
)

const (
	SchemaVersion                 = "1.0"
	JobRequestedEventType         = "job.requested"
	JobCompletedEventType         = "job.completed"
	JobFailedEventType            = "job.failed"
	JobRequestedSubject           = "rainpulse.jobs.requested.model_pysteps_lk"
	RadarDecodeRequestedEventType = "radar.decode.requested.v1"
	RadarDecodeRequestedSubject   = "rainpulse.jobs.requested.radar_decode"
	RadarDecodeJobType            = "radar.decode"
	RadarDecoderVersion           = "cma-rstm-2.0.0"
	JobCompletedSubject           = "rainpulse.jobs.completed"
	JobFailedSubject              = "rainpulse.jobs.failed"
	JobResultsSubject             = "rainpulse.jobs.*"
	JobStreamName                 = "RAINPULSE_JOBS"
	ResultConsumerName            = "rainpulse-orchestrator-results-v2"
	SimulationJobType             = "model.pysteps_lk"
	SimulationModelID             = "pysteps-lk-sim"
	SimulationModelVersion        = "pysteps-lk-sim-v1"
	SimulationConfig              = "rp003-sim-v1"
	SimulationGrid                = "rp003-sim-grid"
)

type JobRequested struct {
	SchemaVersion string              `json:"schema_version"`
	EventID       uuid.UUID           `json:"event_id"`
	EventType     string              `json:"event_type"`
	OccurredAt    time.Time           `json:"occurred_at"`
	RunID         uuid.UUID           `json:"run_id"`
	JobID         uuid.UUID           `json:"job_id"`
	TraceID       uuid.UUID           `json:"trace_id"`
	Payload       JobRequestedPayload `json:"payload"`
}

type JobRequestedPayload struct {
	JobType      string         `json:"job_type"`
	InputURI     string         `json:"input_uri"`
	OutputPrefix string         `json:"output_prefix"`
	GridID       string         `json:"grid_id"`
	Config       string         `json:"config_version"`
	Model        string         `json:"model_version"`
	IssueTime    time.Time      `json:"issue_time"`
	InputAssets  []uuid.UUID    `json:"input_asset_ids,omitempty"`
	Parameters   map[string]any `json:"parameters,omitempty"`
}

type RadarDecodeRequested struct {
	SchemaVersion string                      `json:"schema_version"`
	EventID       uuid.UUID                   `json:"event_id"`
	EventType     string                      `json:"event_type"`
	OccurredAt    time.Time                   `json:"occurred_at"`
	RunID         uuid.UUID                   `json:"run_id"`
	JobID         uuid.UUID                   `json:"job_id"`
	TraceID       uuid.UUID                   `json:"trace_id"`
	Payload       RadarDecodeRequestedPayload `json:"payload"`
}

type RadarDecodeRequestedPayload struct {
	ScanID         uuid.UUID `json:"scan_id"`
	AssetID        uuid.UUID `json:"asset_id"`
	RadarID        string    `json:"radar_id"`
	InputURI       string    `json:"input_uri"`
	OutputPrefix   string    `json:"output_prefix"`
	SourceFormat   string    `json:"source_format"`
	RadarConfig    string    `json:"radar_config_version"`
	DecoderVersion string    `json:"decoder_version"`
}

type JobCompleted struct {
	SchemaVersion string              `json:"schema_version"`
	EventID       uuid.UUID           `json:"event_id"`
	EventType     string              `json:"event_type"`
	OccurredAt    time.Time           `json:"occurred_at"`
	RunID         uuid.UUID           `json:"run_id"`
	JobID         uuid.UUID           `json:"job_id"`
	TraceID       uuid.UUID           `json:"trace_id"`
	Payload       JobCompletedPayload `json:"payload"`
}

type JobCompletedPayload struct {
	Status      string                     `json:"status"`
	StartedAt   time.Time                  `json:"started_at"`
	FinishedAt  time.Time                  `json:"finished_at"`
	RuntimeMS   int64                      `json:"runtime_ms"`
	Assets      []JobCompletedAsset        `json:"assets"`
	Metrics     map[string]float64         `json:"metrics"`
	Diagnostics map[string]json.RawMessage `json:"diagnostics,omitempty"`
}

type JobCompletedAsset struct {
	AssetType string `json:"asset_type"`
	URI       string `json:"uri"`
	SHA256    string `json:"sha256"`
	SizeBytes int64  `json:"size_bytes"`
	MediaType string `json:"media_type,omitempty"`
}

type JobFailed struct {
	SchemaVersion string           `json:"schema_version"`
	EventID       uuid.UUID        `json:"event_id"`
	EventType     string           `json:"event_type"`
	OccurredAt    time.Time        `json:"occurred_at"`
	RunID         uuid.UUID        `json:"run_id"`
	JobID         uuid.UUID        `json:"job_id"`
	TraceID       uuid.UUID        `json:"trace_id"`
	Payload       JobFailedPayload `json:"payload"`
}

type JobFailedPayload struct {
	Status       string         `json:"status"`
	StartedAt    time.Time      `json:"started_at"`
	FinishedAt   time.Time      `json:"finished_at"`
	RuntimeMS    int64          `json:"runtime_ms"`
	ErrorCode    string         `json:"error_code"`
	ErrorMessage string         `json:"error_message"`
	Retryable    bool           `json:"retryable"`
	Details      map[string]any `json:"details"`
}

var sha256Pattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var ErrInvalidEvent = errors.New("invalid event")

func DecodeJobCompleted(data []byte) (JobCompleted, error) {
	var event JobCompleted
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&event); err != nil {
		return JobCompleted{}, fmt.Errorf("%w: decode job.completed: %v", ErrInvalidEvent, err)
	}
	if err := ensureJSONEOF(decoder); err != nil {
		return JobCompleted{}, fmt.Errorf("%w: %v", ErrInvalidEvent, err)
	}
	if err := event.Validate(); err != nil {
		return JobCompleted{}, fmt.Errorf("%w: %v", ErrInvalidEvent, err)
	}
	return event, nil
}

func (event JobCompleted) Validate() error {
	if event.SchemaVersion != SchemaVersion || event.EventType != JobCompletedEventType {
		return fmt.Errorf("unsupported completion event contract")
	}
	if event.EventID == uuid.Nil || event.RunID == uuid.Nil || event.JobID == uuid.Nil || event.TraceID == uuid.Nil {
		return fmt.Errorf("completion event identifiers are required")
	}
	if event.OccurredAt.IsZero() || event.Payload.StartedAt.IsZero() || event.Payload.FinishedAt.IsZero() {
		return fmt.Errorf("completion event timestamps are required")
	}
	if event.Payload.Status != "succeeded" {
		return fmt.Errorf("unsupported completion status %q", event.Payload.Status)
	}
	if event.Payload.RuntimeMS < 0 || event.Payload.FinishedAt.Before(event.Payload.StartedAt) {
		return fmt.Errorf("invalid completion runtime")
	}
	for _, asset := range event.Payload.Assets {
		parsed, err := url.ParseRequestURI(asset.URI)
		if err != nil || parsed.Scheme == "" || asset.AssetType == "" || !sha256Pattern.MatchString(asset.SHA256) || asset.SizeBytes < 0 {
			return fmt.Errorf("invalid completion asset")
		}
	}
	return nil
}

func DecodeJobFailed(data []byte) (JobFailed, error) {
	var event JobFailed
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&event); err != nil {
		return JobFailed{}, fmt.Errorf("%w: decode job.failed: %v", ErrInvalidEvent, err)
	}
	if err := ensureJSONEOF(decoder); err != nil {
		return JobFailed{}, fmt.Errorf("%w: %v", ErrInvalidEvent, err)
	}
	if err := event.Validate(); err != nil {
		return JobFailed{}, fmt.Errorf("%w: %v", ErrInvalidEvent, err)
	}
	return event, nil
}

func (event JobFailed) Validate() error {
	if event.SchemaVersion != SchemaVersion || event.EventType != JobFailedEventType {
		return fmt.Errorf("unsupported failure event contract")
	}
	if event.EventID == uuid.Nil || event.RunID == uuid.Nil || event.JobID == uuid.Nil || event.TraceID == uuid.Nil {
		return fmt.Errorf("failure event identifiers are required")
	}
	if event.OccurredAt.IsZero() || event.Payload.StartedAt.IsZero() || event.Payload.FinishedAt.IsZero() {
		return fmt.Errorf("failure event timestamps are required")
	}
	if event.Payload.Status != "failed" {
		return fmt.Errorf("unsupported failure status %q", event.Payload.Status)
	}
	if event.Payload.RuntimeMS < 0 || event.Payload.FinishedAt.Before(event.Payload.StartedAt) {
		return fmt.Errorf("invalid failure runtime")
	}
	if len(event.Payload.ErrorCode) == 0 || len(event.Payload.ErrorCode) > 128 {
		return fmt.Errorf("invalid failure error code")
	}
	if len(event.Payload.ErrorMessage) == 0 || len(event.Payload.ErrorMessage) > 2048 {
		return fmt.Errorf("invalid failure error message")
	}
	if event.Payload.Details == nil {
		return fmt.Errorf("failure details are required")
	}
	return nil
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); err == io.EOF {
		return nil
	} else if err != nil {
		return fmt.Errorf("decode trailing completion data: %w", err)
	}
	return fmt.Errorf("completion event contains trailing JSON")
}

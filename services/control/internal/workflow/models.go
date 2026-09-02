package workflow

import (
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"
)

var ErrNotFound = errors.New("workflow record not found")

type Run struct {
	ID             uuid.UUID
	IssueTime      time.Time
	GridID         string
	ConfigVersion  string
	Status         RunStatus
	DegradedReason *string
	RerunOf        *uuid.UUID
	Reason         string
	CreatedAt      time.Time
	UpdatedAt      time.Time
}

type Job struct {
	ID             uuid.UUID
	RunID          uuid.UUID
	TraceID        uuid.UUID
	JobType        string
	ModelID        string
	ModelVersion   string
	ConfigVersion  string
	Status         JobStatus
	Attempt        int
	StartedAt      *time.Time
	FinishedAt     *time.Time
	RuntimeMS      *int64
	ErrorCode      *string
	ErrorMessage   *string
	RequestPayload json.RawMessage
	CreatedAt      time.Time
}

type OutboxEvent struct {
	ID          uuid.UUID
	AggregateID string
	EventType   string
	Subject     string
	Payload     json.RawMessage
	Attempts    int
}

type CreateBundle struct {
	Run    Run
	Job    Job
	Outbox OutboxEvent
}

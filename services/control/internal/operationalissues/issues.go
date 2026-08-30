package operationalissues

import (
	"context"
	"time"
)

type Kind string

const (
	KindJob    Kind = "job"
	KindOutbox Kind = "outbox"
)

type Counts struct {
	Total        int
	FailedJobs   int
	StuckJobs    int
	OutboxEvents int
}

type Issue struct {
	ID           string
	Kind         Kind
	Status       string
	Summary      string
	RunID        *string
	JobID        *string
	EventID      *string
	AggregateID  *string
	JobType      *string
	EventType    *string
	ErrorCode    *string
	ErrorMessage *string
	AttemptCount int
	AgeSeconds   float64
	CreatedAt    time.Time
	UpdatedAt    time.Time
}

type Snapshot struct {
	Counts     Counts
	Items      []Issue
	ObservedAt time.Time
}

type Reader interface {
	OperationalIssues(context.Context, int) (Snapshot, error)
}

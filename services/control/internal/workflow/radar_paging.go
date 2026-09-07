package workflow

import (
	"time"

	"github.com/google/uuid"
)

type RadarScanListCursor struct {
	VolumeEndTime time.Time
	ScanID        uuid.UUID
}

type RadarScanListQuery struct {
	Limit        int
	RadarID      *string
	Status       *RadarScanStatus
	StartTime    *time.Time
	EndTime      *time.Time
	SnapshotTime time.Time
	Cursor       *RadarScanListCursor
}

type RadarScanListPage struct {
	Items        []RadarScan
	NextCursor   *RadarScanListCursor
	SnapshotTime time.Time
}

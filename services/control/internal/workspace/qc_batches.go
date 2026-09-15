package workspace

import (
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"net/http"
	"time"
)

type QCBatchItem struct {
	Kind   string     `json:"kind"`
	ID     uuid.UUID  `json:"id"`
	Time   time.Time  `json:"time"`
	Radar  string     `json:"radar"`
	JobID  *uuid.UUID `json:"job_id,omitempty"`
	Status string     `json:"status"`
	Error  string     `json:"error"`
}
type QCBatch struct {
	ID     uuid.UUID     `json:"request_id"`
	Status string        `json:"status"`
	Items  []QCBatchItem `json:"items"`
}
type QCBatchStore interface {
	HistoricalQCBatch(context.Context, time.Time, bool) (*QCBatch, error)
}

func (h *runtimeHandler) qcBatch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		w.WriteHeader(405)
		return
	}
	if r.Method == http.MethodPost && !h.authorized(r) {
		runtimeWriteError(w, 401, "unauthorized", "administrator credentials are required")
		return
	}
	s, ok := h.store.(QCBatchStore)
	if !ok {
		runtimeWriteError(w, 503, "unavailable", "QC batch store unavailable")
		return
	}
	date := r.URL.Query().Get("date")
	if r.Method == http.MethodPost {
		var p struct {
			Date string `json:"date"`
		}
		d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
		d.DisallowUnknownFields()
		if d.Decode(&p) != nil {
			runtimeWriteError(w, 400, "invalid_request", "date is required")
			return
		}
		date = p.Date
	}
	t, e := time.ParseInLocation("2006-01-02", date, time.FixedZone("Beijing", 8*3600))
	if e != nil {
		runtimeWriteError(w, 400, "invalid_date", "expected YYYY-MM-DD")
		return
	}
	b, e := s.HistoricalQCBatch(r.Context(), t, r.Method == http.MethodPost)
	if e != nil {
		runtimeWriteError(w, 409, "qc_batch_error", e.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(b)
}

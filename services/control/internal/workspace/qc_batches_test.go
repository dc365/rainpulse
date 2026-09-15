package workspace

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type qcStore struct {
	RuntimeStore
	called bool
	start  time.Time
}

func (s *qcStore) HistoricalQCBatch(_ context.Context, t time.Time, _ bool) (*QCBatch, error) {
	s.called = true
	s.start = t
	return &QCBatch{Status: "PENDING"}, nil
}
func TestQCBatchAuthorizationAndBeijingDate(t *testing.T) {
	s := &qcStore{}
	h := &runtimeHandler{store: s, adminToken: "secret"}
	r := httptest.NewRequest("POST", "/api/v1/admin/qc-batches", strings.NewReader(`{"date":"2026-08-28"}`))
	w := httptest.NewRecorder()
	h.qcBatch(w, r)
	if w.Code != 401 || s.called {
		t.Fatal("unauthorized batch accepted")
	}
	r = httptest.NewRequest("POST", "/api/v1/admin/qc-batches", strings.NewReader(`{"date":"2026-08-28"}`))
	r.Header.Set("Authorization", "Bearer secret")
	w = httptest.NewRecorder()
	h.qcBatch(w, r)
	if w.Code != 200 || s.start.UTC().Format(time.RFC3339) != "2026-08-27T16:00:00Z" {
		t.Fatalf("wrong date/status: %v %d", s.start, w.Code)
	}
}

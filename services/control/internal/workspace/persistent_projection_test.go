package workspace

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

type memoryProjectionStore struct {
	record ProjectionRecord
	err    error
	saves  int
}

func (store *memoryProjectionStore) LoadWorkspaceProjection(_ context.Context, key string) (ProjectionRecord, error) {
	if store.err != nil {
		return ProjectionRecord{}, store.err
	}
	if store.record.Key != key {
		return ProjectionRecord{}, ErrProjectionNotFound
	}
	return store.record, nil
}
func (store *memoryProjectionStore) SaveWorkspaceProjection(_ context.Context, record ProjectionRecord) error {
	store.record = record
	store.saves++
	return nil
}
func (store *memoryProjectionStore) DeleteExpiredWorkspaceProjections(context.Context, time.Time, int) error {
	return nil
}

func TestPersistentProjectionReusesWorkspaceJSONAndETag(t *testing.T) {
	now := time.Date(2026, 9, 3, 8, 0, 0, 0, time.UTC)
	calls := 0
	next := http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		calls++
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"cycle":1}`))
	})
	store := &memoryProjectionStore{err: ErrProjectionNotFound}
	handler := WithPersistentProjection(next, store, PersistentProjectionOptions{
		CatalogTTL: time.Minute, CycleTTL: time.Minute, Now: func() time.Time { return now },
	})

	first := httptest.NewRecorder()
	handler.ServeHTTP(first, httptest.NewRequest(http.MethodGet, workspacePrefix, nil))
	if first.Code != http.StatusOK || calls != 1 || store.saves != 1 {
		t.Fatalf("first response = %d, calls=%d saves=%d", first.Code, calls, store.saves)
	}
	store.err = nil
	secondRequest := httptest.NewRequest(http.MethodGet, workspacePrefix, nil)
	secondRequest.Header.Set("If-None-Match", store.record.ETag)
	second := httptest.NewRecorder()
	handler.ServeHTTP(second, secondRequest)
	if second.Code != http.StatusNotModified || calls != 1 {
		t.Fatalf("second response = %d, calls=%d", second.Code, calls)
	}
}

func TestPersistentProjectionFallsBackWhenStoreUnavailable(t *testing.T) {
	store := &memoryProjectionStore{err: errors.New("database unavailable")}
	next := http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.WriteHeader(http.StatusOK)
	})
	handler := WithPersistentProjection(next, store, PersistentProjectionOptions{})
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, workspacePrefix, nil))
	if response.Code != http.StatusOK {
		t.Fatalf("response = %d", response.Code)
	}
}

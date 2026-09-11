package unified

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestRetryIsSerialAndCancellationStopsIt(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	var active, maximum, calls atomic.Int32
	done := make(chan struct{})
	go func() {
		defer close(done)
		retry(ctx, time.Millisecond, func(context.Context) error {
			n := active.Add(1)
			if n > maximum.Load() {
				maximum.Store(n)
			}
			defer active.Add(-1)
			if calls.Add(1) == 3 {
				cancel()
			}
			return errors.New("offline")
		}, func(error) {})
	}()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("did not stop")
	}
	if maximum.Load() != 1 || calls.Load() != 3 {
		t.Fatal("nonserial retry")
	}
}

func TestModuleFailureDoesNotStopHTTP(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	called := make(chan struct{}, 1)
	go retry(ctx, time.Hour, func(context.Context) error { called <- struct{}{}; return errors.New("missing radar directory") }, func(error) {})
	<-called
	w := httptest.NewRecorder()
	http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(200) }).ServeHTTP(w, httptest.NewRequest("GET", "/", nil))
	if w.Code != 200 || ctx.Err() != nil {
		t.Fatal("module failure killed context")
	}
}

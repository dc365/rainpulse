package releaseguard

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestMissingAllowsAndPausedRejects(t *testing.T) {
	path := filepath.Join(t.TempDir(), "release-gate.json")
	done, err := AcquireAt(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	done()
	if err := os.WriteFile(path, []byte(`{"schema_version":1,"state":"paused","release_id":"test"}`), 0640); err != nil {
		t.Fatal(err)
	}
	_, err = AcquireAt(context.Background(), path)
	if !errors.Is(err, ErrPaused) {
		t.Fatal(err)
	}
}

func TestMalformedGateFailsClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gate")
	for _, data := range []string{"", "{}", "false", `{"schema_version":1,"state":"active","release_id":"x"}`} {
		_ = os.WriteFile(path, []byte(data), 0640)
		if _, err := AcquireAt(context.Background(), path); err == nil {
			t.Fatal(data)
		}
	}
}

func TestLeaseExcludesPauseAndWaitHonorsCancellation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gate")
	release, err := AcquireAt(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	file, err := os.OpenFile(path+".lock", os.O_RDWR, 0640)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err == nil {
		t.Fatal("exclusive lock acquired during admitted work")
	}
	release()
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	defer syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	if _, err := AcquireAt(ctx, path); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
}

func TestReadOnlyAPIWorksWhileMutationsPause(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gate")
	t.Setenv("RAINPULSE_RELEASE_GATE_FILE", path)
	_ = os.WriteFile(path, []byte(`{"schema_version":1,"state":"paused","release_id":"test"}`), 0640)
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	for method, want := range map[string]int{"GET": 204, "HEAD": 204, "POST": 503, "DELETE": 503} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest(method, "/", nil))
		if rec.Code != want {
			t.Fatalf("%s=%d", method, rec.Code)
		}
	}
}

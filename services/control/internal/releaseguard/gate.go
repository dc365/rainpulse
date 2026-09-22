// Package releaseguard implements a local admission pause for coordinated
// deployment. It does not acknowledge, cancel, delete or rewrite any queued job.
package releaseguard

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

var ErrPaused = errors.New("deployment release has paused new work")

func Path() string {
	if path := os.Getenv("RAINPULSE_RELEASE_GATE_FILE"); path != "" {
		return path
	}
	return filepath.Join("runtime", "control", "release-gate.json")
}

// Acquire uses the same flock file as rainpulsectl. A successful pause waits for
// admitted planner ticks / mutating HTTP calls to leave this shared lease.
// Results, reads and worker acknowledgements remain live while admission pauses.
func Acquire(ctx context.Context) (func(), error) { return AcquireAt(ctx, Path()) }

func AcquireAt(ctx context.Context, path string) (func(), error) {
	if err := os.MkdirAll(filepath.Dir(path), 0750); err != nil {
		return nil, fmt.Errorf("create release guard directory: %w", err)
	}
	lock, err := os.OpenFile(path+".lock", os.O_CREATE|os.O_RDWR, 0640)
	if err != nil {
		return nil, fmt.Errorf("open release guard lock: %w", err)
	}
	release := func() { _ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); _ = lock.Close() }
	for {
		err = syscall.Flock(int(lock.Fd()), syscall.LOCK_SH|syscall.LOCK_NB)
		if err == nil {
			break
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			release()
			return nil, err
		}
		select {
		case <-ctx.Done():
			release()
			return nil, ctx.Err()
		case <-time.After(20 * time.Millisecond):
		}
	}
	if err := ctx.Err(); err != nil {
		release()
		return nil, err
	}
	paused, err := readPause(path)
	if err != nil {
		release()
		return nil, err
	}
	if paused {
		release()
		return nil, ErrPaused
	}
	return release, nil
}

func readPause(path string) (bool, error) {
	file, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("read release gate: %w", err)
	}
	defer file.Close()
	bytes, err := io.ReadAll(io.LimitReader(file, 16385))
	if err != nil {
		return false, err
	}
	if len(bytes) > 16384 {
		return false, fmt.Errorf("release gate exceeds 16 KiB")
	}
	var state struct {
		SchemaVersion int    `json:"schema_version"`
		State         string `json:"state"`
		ReleaseID     string `json:"release_id"`
	}
	if err := json.Unmarshal(bytes, &state); err != nil {
		return false, fmt.Errorf("invalid release gate (new work remains blocked): %w", err)
	}
	if state.SchemaVersion != 1 || state.State != "paused" || state.ReleaseID == "" {
		return false, fmt.Errorf("invalid release gate identity; manual inspection required")
	}
	return true, nil
}

// Wrap guards new browser/API commands. Cancellation is also paused so the
// drained inventory cannot change during final verification; operators may
// explicitly abandon a release and resume the old installation instead.
func Wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet, http.MethodHead, http.MethodOptions:
			next.ServeHTTP(w, r)
			return
		}
		release, err := Acquire(r.Context())
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Retry-After", "5")
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = io.WriteString(w, `{"code":"release_admission_paused","message":"New work is paused for deployment; reads and worker result processing remain available."}`)
			return
		}
		defer release()
		next.ServeHTTP(w, r)
	})
}

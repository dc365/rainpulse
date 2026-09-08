package workspace

import (
	"context"
	"sync/atomic"
	"testing"
	"time"
)

func TestWorkspaceRevisionIgnoresClockButNotCapabilities(t *testing.T) {
	a, err := workspaceRevision([]byte(`{"generated_at":"a","items":[{"cycle_id":"1","freshness_seconds":1,"capabilities":{"lk":false}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	b, _ := workspaceRevision([]byte(`{"generated_at":"b","items":[{"cycle_id":"1","freshness_seconds":12,"capabilities":{"lk":false}}]}`))
	c, _ := workspaceRevision([]byte(`{"items":[{"cycle_id":"1","capabilities":{"lk":true}}]}`))
	if a != b || b == c {
		t.Fatal("unstable or incomplete revision")
	}
	if _, err := workspaceRevision([]byte(`invalid`)); err == nil {
		t.Fatal("accepted invalid JSON")
	}
}
func TestWorkspaceSubscribersShareOneCatalogReader(t *testing.T) {
	var calls atomic.Int32
	h := newWorkspaceEventHub(time.Hour, func(context.Context) (string, error) { calls.Add(1); return "one", nil })
	var cleanups []func()
	for i := 0; i < 24; i++ {
		updates, cleanup := h.subscribe()
		cleanups = append(cleanups, cleanup)
		select {
		case revision := <-updates:
			if revision != "one" {
				t.Fatal(revision)
			}
		case <-time.After(time.Second):
			t.Fatal("no initial notification")
		}
	}
	if calls.Load() != 1 {
		t.Fatalf("24 subscribers caused %d source reads", calls.Load())
	}
	for _, cleanup := range cleanups {
		cleanup()
		cleanup()
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.subscribers) != 0 || h.cancel != nil {
		t.Fatal("idle producer not released")
	}
}
func TestWorkspaceSlowSubscriberReceivesLatestRevision(t *testing.T) {
	h := newWorkspaceEventHub(time.Hour, func(context.Context) (string, error) { return "one", nil })
	updates, cleanup := h.subscribe()
	defer cleanup()
	select {
	case <-updates:
	case <-time.After(time.Second):
		t.Fatal("no initial revision")
	}
	h.mu.Lock()
	generation := h.generation
	h.mu.Unlock()
	h.broadcast(generation, "two")
	h.broadcast(generation, "three")
	if got := <-updates; got != "three" {
		t.Fatalf("got %s", got)
	}
}

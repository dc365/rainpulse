package workspace

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"sync"
	"time"
)

// One bounded producer per API process, not a polling loop for every browser.
// Ordinary projection caches remain authoritative for the refresh interval.
type workspaceEventHub struct {
	mu          sync.Mutex
	subscribers map[chan string]struct{}
	source      func(context.Context) (string, error)
	interval    time.Duration
	cancel      context.CancelFunc
	generation  uint64
	latest      string
}

func newWorkspaceEventHub(interval time.Duration, source func(context.Context) (string, error)) *workspaceEventHub {
	if interval < 100*time.Millisecond {
		interval = 2 * time.Second
	}
	return &workspaceEventHub{source: source, interval: interval, subscribers: make(map[chan string]struct{})}
}
func (h *workspaceEventHub) subscribe() (<-chan string, func()) {
	h.mu.Lock()
	updates := make(chan string, 1)
	h.subscribers[updates] = struct{}{}
	if h.latest != "" {
		updates <- h.latest
	}
	if h.cancel == nil {
		ctx, cancel := context.WithCancel(context.Background())
		h.cancel = cancel
		h.generation++
		go h.run(ctx, h.generation)
	}
	h.mu.Unlock()
	var once sync.Once
	return updates, func() {
		once.Do(func() {
			h.mu.Lock()
			defer h.mu.Unlock()
			delete(h.subscribers, updates)
			close(updates)
			if len(h.subscribers) == 0 && h.cancel != nil {
				h.cancel()
				h.cancel = nil
				h.generation++
				h.latest = ""
			}
		})
	}
}
func (h *workspaceEventHub) run(ctx context.Context, generation uint64) {
	ticker := time.NewTicker(h.interval)
	defer ticker.Stop()
	for {
		bounded, cancel := context.WithTimeout(ctx, 10*time.Second)
		revision, err := h.source(bounded)
		cancel()
		if ctx.Err() != nil {
			return
		}
		if err != nil {
			revision = "catalog-unavailable"
		}
		h.broadcast(generation, revision)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
func (h *workspaceEventHub) broadcast(generation uint64, revision string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if generation != h.generation || revision == h.latest {
		return
	}
	h.latest = revision
	for updates := range h.subscribers {
		select {
		case <-updates:
		default:
		}
		updates <- revision
	}
}
func workspaceRevision(body []byte) (string, error) {
	var value map[string]json.RawMessage
	if err := json.Unmarshal(body, &value); err != nil {
		return "", err
	}
	if value == nil {
		return "", fmt.Errorf("workspace catalog must be an object")
	}
	delete(value, "generated_at")
	var items []map[string]json.RawMessage
	if err := json.Unmarshal(value["items"], &items); err != nil {
		return "", err
	}
	for _, item := range items {
		delete(item, "freshness_seconds")
	}
	normalized, err := json.Marshal(items)
	if err != nil {
		return "", err
	}
	value["items"] = normalized
	canonical, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", sha256.Sum256(canonical)), nil
}

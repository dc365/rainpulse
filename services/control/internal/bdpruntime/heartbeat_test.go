package bdpruntime

import (
	"context"
	"testing"
	"time"
)

func TestHeartbeatContinuesAndStopsWithServer(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	beats := make(chan struct{}, 4)
	runtime := Runtime{heartbeat: func() { beats <- struct{}{} }}
	done := make(chan struct{})
	go func() { runtime.MaintainHeartbeat(ctx); close(done) }()
	for i := 0; i < 2; i++ {
		select {
		case <-beats:
		case <-time.After(3 * time.Second):
			t.Fatal("missing recurring heartbeat")
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("heartbeat did not stop")
	}
	// The compatibility runtime needs no platform initialization.
	Runtime{}.MaintainHeartbeat(context.Background())
}

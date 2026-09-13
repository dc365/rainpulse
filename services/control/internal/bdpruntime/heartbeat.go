package bdpruntime

import (
	"context"
	"time"
)

// MaintainHeartbeat reports process liveness for a long-running unified server.
// Job health remains independently tracked by the control plane. Unregistered
// and non-BDP runtimes do not start a platform heartbeat.
func (runtime Runtime) MaintainHeartbeat(ctx context.Context) {
	if runtime.heartbeat == nil {
		return
	}
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			runtime.heartbeat()
		}
	}
}

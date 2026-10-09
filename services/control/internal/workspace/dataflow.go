package workspace

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const (
	dataflowDefaultWindowMinutes = 60
	dataflowMinWindowMinutes     = 10
	dataflowMaxWindowMinutes     = 360
	dataflowTrendPath            = "/api/v1/workspace/dataflow/stage-trend"
	dataflowDefaultTrendHours    = 6
	dataflowMinTrendHours        = 1
	dataflowMaxTrendHours        = 24
)

// getDataflow serves the realtime dataflow screen projection. The window is a
// wall-clock lookback clamped to a sane range; `end` optionally anchors the
// window's right edge at a past moment for historical replay of a recorded
// case. The store owns all aggregation.
func (handler *runtimeHandler) getDataflow(response http.ResponseWriter, request *http.Request) {
	if handler.store == nil {
		runtimeWriteError(response, http.StatusServiceUnavailable, "dataflow_store_unavailable", "dataflow snapshot store is unavailable")
		return
	}
	window := dataflowWindowMinutes(request.URL.Query().Get("window"))
	anchor := handler.now().UTC()
	if rawEnd := strings.TrimSpace(request.URL.Query().Get("end")); rawEnd != "" {
		parsed, err := time.Parse(time.RFC3339, rawEnd)
		if err != nil {
			runtimeWriteError(response, http.StatusBadRequest, "invalid_dataflow_end", "end must be an RFC3339 timestamp")
			return
		}
		parsed = parsed.UTC()
		if !parsed.Before(anchor.Add(-dataflowMinWindowMinutes * time.Minute)) {
			runtimeWriteError(response, http.StatusBadRequest, "invalid_dataflow_end", "end must be in the past")
			return
		}
		anchor = parsed
	}
	snapshot, err := handler.store.WorkspaceDataflowSnapshot(
		request.Context(), anchor, time.Duration(window)*time.Minute,
	)
	if err != nil {
		runtimeWriteError(response, http.StatusServiceUnavailable, "dataflow_snapshot_unavailable", err.Error())
		return
	}
	response.Header().Set("Cache-Control", "no-store")
	writeJSON(response, http.StatusOK, snapshot)
}

// streamDataflowEvents is the SSE revision ping for the dataflow screen. It
// follows the workspace catalog stream: subscribers get dataflow.changed with a
// revision token whenever any table the screen renders has moved, then refetch
// the bounded snapshot themselves.
func (handler *runtimeHandler) streamDataflowEvents(response http.ResponseWriter, request *http.Request) {
	flusher, ok := response.(http.Flusher)
	if !ok {
		runtimeWriteError(response, http.StatusInternalServerError, "streaming_unavailable", "HTTP streaming is unavailable")
		return
	}
	handler.dataflowHubOnce.Do(func() {
		handler.dataflowHub = newWorkspaceEventHub(
			environmentDuration("RAINPULSE_DATAFLOW_EVENT_INTERVAL", 2*time.Second),
			func(ctx context.Context) (string, error) {
				if handler.store == nil {
					return "", errors.New("dataflow revision store is unavailable")
				}
				return handler.store.WorkspaceDataflowRevision(ctx)
			},
		)
	})
	response.Header().Set("Content-Type", "text/event-stream")
	response.Header().Set("Cache-Control", "no-cache, no-transform")
	response.Header().Set("X-Accel-Buffering", "no")
	updates, unsubscribe := handler.dataflowHub.subscribe()
	defer unsubscribe()
	heartbeat := time.NewTicker(15 * time.Second)
	defer heartbeat.Stop()
	if _, err := fmt.Fprint(response, ": connected\n\n"); err != nil {
		return
	}
	flusher.Flush()
	for {
		select {
		case <-request.Context().Done():
			return
		case revision, open := <-updates:
			if !open {
				return
			}
			payload, _ := json.Marshal(map[string]string{"event": "dataflow.changed", "revision": revision})
			if _, err := fmt.Fprintf(response, "id: %s\nevent: dataflow.changed\ndata: %s\n\n", revision, payload); err != nil {
				return
			}
			flusher.Flush()
		case <-heartbeat.C:
			if _, err := fmt.Fprint(response, ": heartbeat\n\n"); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

// dataflowWindowHours clamps the trend lookback; six hours covers a full
// operational shift at the six-minute beat.
func dataflowWindowHours(raw string) int {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return dataflowDefaultTrendHours
	}
	parsed, err := strconv.Atoi(trimmed)
	if err != nil {
		return dataflowDefaultTrendHours
	}
	if parsed < dataflowMinTrendHours {
		return dataflowMinTrendHours
	}
	if parsed > dataflowMaxTrendHours {
		return dataflowMaxTrendHours
	}
	return parsed
}

// getDataflowStageTrend serves the sparkline history: per-stage median
// runtimes bucketed on the wall clock, straight from the jobs table.
func (handler *runtimeHandler) getDataflowStageTrend(response http.ResponseWriter, request *http.Request) {
	if handler.store == nil {
		runtimeWriteError(response, http.StatusServiceUnavailable, "dataflow_store_unavailable", "dataflow snapshot store is unavailable")
		return
	}
	hours := dataflowWindowHours(request.URL.Query().Get("hours"))
	trend, err := handler.store.WorkspaceDataflowStageTrend(request.Context(), handler.now().UTC(), hours)
	if err != nil {
		runtimeWriteError(response, http.StatusServiceUnavailable, "dataflow_trend_unavailable", err.Error())
		return
	}
	response.Header().Set("Cache-Control", "no-store")
	writeJSON(response, http.StatusOK, trend)
}

func dataflowWindowMinutes(raw string) int {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return dataflowDefaultWindowMinutes
	}
	parsed, err := strconv.Atoi(trimmed)
	if err != nil {
		return dataflowDefaultWindowMinutes
	}
	if parsed < dataflowMinWindowMinutes {
		return dataflowMinWindowMinutes
	}
	if parsed > dataflowMaxWindowMinutes {
		return dataflowMaxWindowMinutes
	}
	return parsed
}

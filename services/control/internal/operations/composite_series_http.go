package operations

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"
)

// Read-only versioned envelope; no regrouping of existing frozen product IDs.
func (h *Handler) radarCompositeSeries(r *http.Request) (any, error) {
	start, end, err := parseWindow(r.URL.Query(), 24*time.Hour)
	if err != nil {
		return nil, err
	}
	selected, err := compositeSeriesQuery(r.URL.Query())
	if err != nil {
		return nil, err
	}
	if target := r.URL.Query().Get("target"); target != "" {
		at, e := time.Parse(time.RFC3339Nano, target)
		if e != nil || at.Before(start) || !at.Before(end) {
			return nil, Invalid("target outside catalog window")
		}
	}
	var raw json.RawMessage
	query := strings.Replace(compositeTimelineSQL, "LIMIT 1500", "LIMIT 1501", 1)
	if err = h.service.Store.DB.QueryRowContext(r.Context(), query, start, end, selected).Scan(&raw); err != nil {
		return nil, err
	}
	page, err := compositeSeriesEnvelope(raw)
	if err != nil {
		return nil, Conflict(err.Error())
	}
	return page, nil
}

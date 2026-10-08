package operations

import (
	"encoding/json"
	"fmt"
	"strings"
)

// Reuse the published v2 digest verbatim: frozen S QC policy, the complete
// worker identity and legacy run isolation must also govern the new envelope.
var compositeSeriesEligibleSQL = strings.SplitN(compositeTimelineSQL, "), series AS (", 2)[0] + ") "

func compositeSeriesEnvelope(raw []byte) (map[string]any, error) {
	if len(raw) > 32<<20 {
		return nil, fmt.Errorf("composite catalog exceeds metadata budget")
	}
	var page map[string]any
	if json.Unmarshal(raw, &page) != nil || page == nil {
		return nil, fmt.Errorf("invalid composite catalog")
	}
	items, ok := page["items"].([]any)
	if !ok {
		return nil, fmt.Errorf("invalid composite catalog items")
	}
	selected, _ := page["selected_series_id"].(string)
	truncated := len(items) > 1500
	if truncated {
		items = items[:1500]
	}
	page["items"] = items
	page["series_id"] = selected
	page["contract"] = "rainpulse.sx-series-v1"
	page["truncated"] = truncated
	series, _ := page["series"].([]any)
	for _, entry := range series {
		s, ok := entry.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("invalid composite catalog series")
		}
		// Only the selected series was enumerated. Unknown counts stay null.
		s["frame_count"] = nil
		s["count_complete"] = false
		if s["series_id"] == selected {
			s["frame_count"] = len(items)
			s["count_complete"] = !truncated
		}
	}
	return page, nil
}

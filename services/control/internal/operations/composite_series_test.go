package operations

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestSXSeriesUsesPublishedFrozenIdentity(t *testing.T) {
	for _, fragment := range []string{"'sx-configured-series-v2'", "'identity',t.spec->'identity'", "'s_qc_policy'", "s_qc_policy_complete", "'legacy_run_id'", "t.run_id::text", "ops_retired_runs"} {
		if !strings.Contains(compositeSeriesEligibleSQL, fragment) {
			t.Fatalf("missing identity boundary %s", fragment)
		}
	}
	if strings.Contains(compositeSeriesEligibleSQL, "series AS") {
		t.Fatal("eligible query contains subsequent CTE")
	}
}
func TestSXSeriesEnvelopePreservesPublishedIDsAndUnknownCounts(t *testing.T) {
	raw := []byte(`{"selected_series_id":"published-id","items":[{"result_id":"r","analysis_time":"t","series_id":"published-id"}],"series":[{"series_id":"published-id"},{"series_id":"other"}]}`)
	p, e := compositeSeriesEnvelope(raw)
	if e != nil {
		t.Fatal(e)
	}
	if p["series_id"] != "published-id" || p["truncated"] != false {
		t.Fatal(p)
	}
	series := p["series"].([]any)
	if series[0].(map[string]any)["frame_count"] != 1 || series[1].(map[string]any)["frame_count"] != nil {
		t.Fatal(series)
	}
}
func TestSXSeriesEnvelopeTruncationAndExplicitMissing(t *testing.T) {
	items := make([]any, 1501)
	for i := range items {
		items[i] = map[string]any{"series_id": "p"}
	}
	raw, _ := json.Marshal(map[string]any{"selected_series_id": "p", "items": items, "series": []any{map[string]any{"series_id": "p"}}})
	p, e := compositeSeriesEnvelope(raw)
	if e != nil {
		t.Fatal(e)
	}
	if !p["truncated"].(bool) || len(p["items"].([]any)) != 1500 || p["series"].([]any)[0].(map[string]any)["count_complete"] != false {
		t.Fatal("truncation not reported")
	}
	p, e = compositeSeriesEnvelope([]byte(`{"selected_series_id":"missing","items":[],"series":[{"series_id":"other"}]}`))
	if e != nil || len(p["items"].([]any)) != 0 || p["series_id"] != "missing" {
		t.Fatal("foreign fallback", p, e)
	}
}
func TestSXSeriesEnvelopeRejectsMalformed(t *testing.T) {
	for _, raw := range []string{"{", "null", `{}`, `{"items":null}`} {
		if _, e := compositeSeriesEnvelope([]byte(raw)); e == nil {
			t.Fatal(raw)
		}
	}
}

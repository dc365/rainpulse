package workspace

import (
	"context"
	"net/url"
)

// Explicit compatibility adapter for NewHandler(core) callers and existing
// HTTP-fixture tests. Native apiapp always injects readquery.Service instead.
func (h *Handler) legacyForecastPage(ctx context.Context, status, cursor string) (forecastRunPage, error) {
	path := "/api/v1/runs?status=" + url.QueryEscape(status) + "&limit=100"
	if cursor != "" {
		path += "&cursor=" + url.QueryEscape(cursor)
	}
	var page forecastRunPage
	err := h.readCore(ctx, path, &page)
	return page, err
}
func (h *Handler) legacyAnalysisPage(ctx context.Context, cursor string) (analysisCyclePage, error) {
	path := "/api/v1/analysis-cycles?status=ANALYSIS_READY&limit=200"
	if cursor != "" {
		path += "&cursor=" + url.QueryEscape(cursor)
	}
	var page analysisCyclePage
	err := h.readCore(ctx, path, &page)
	return page, err
}
func (h *Handler) legacyAnalysis(ctx context.Context, id string) (analysisCycle, error) {
	var value analysisCycle
	err := h.readCore(ctx, "/api/v1/analysis-cycles/"+url.PathEscape(id), &value)
	return value, err
}
func (h *Handler) legacyQPE(ctx context.Context, id string) (qpeSummary, error) {
	var value qpeSummary
	err := h.readCore(ctx, "/api/v1/analysis-cycles/"+url.PathEscape(id)+"/qpe-summary", &value)
	return value, err
}
func (h *Handler) legacyDiagnostics(ctx context.Context, id string) (diagnosticBundle, error) {
	var value diagnosticBundle
	err := h.readCore(ctx, "/api/v1/analysis-cycles/"+url.PathEscape(id)+"/diagnostics", &value)
	return value, err
}

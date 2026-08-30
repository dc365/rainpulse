package alerting

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"
)

const maximumUpstreamBodyBytes = 2 << 20

type SnapshotStatus string

const (
	SnapshotReady    SnapshotStatus = "ready"
	SnapshotDegraded SnapshotStatus = "degraded"
)

type SourceAvailability string

const (
	SourceReady       SourceAvailability = "ready"
	SourceUnavailable SourceAvailability = "unavailable"
)

type State string

const (
	StatePending   State = "pending"
	StateFiring    State = "firing"
	StateSilenced  State = "silenced"
	StateInhibited State = "inhibited"
)

type Severity string

const (
	SeverityInfo     Severity = "info"
	SeverityWarning  Severity = "warning"
	SeverityCritical Severity = "critical"
	SeverityUnknown  Severity = "unknown"
)

type Sources struct {
	Prometheus   SourceAvailability
	Alertmanager SourceAvailability
}

type Counts struct {
	Total     int
	Pending   int
	Firing    int
	Silenced  int
	Inhibited int
}

type Item struct {
	ID          string
	Name        string
	Severity    Severity
	State       State
	Summary     string
	ActiveAt    time.Time
	Value       *string
	Labels      map[string]string
	Annotations map[string]string
}

type Snapshot struct {
	Status     SnapshotStatus
	Sources    Sources
	Counts     Counts
	Items      []Item
	ObservedAt time.Time
}

type Reader interface {
	Snapshot(context.Context) Snapshot
}

type Options struct {
	HTTPClient *http.Client
	Now        func() time.Time
}

type Client struct {
	prometheusAlertsURL   string
	alertmanagerAlertsURL string
	httpClient            *http.Client
	now                   func() time.Time
}

func NewClient(prometheusBaseURL string, alertmanagerBaseURL string, options Options) (*Client, error) {
	prometheusAlertsURL, err := resolveEndpoint(prometheusBaseURL, "/api/v1/alerts")
	if err != nil {
		return nil, fmt.Errorf("configure Prometheus alert endpoint: %w", err)
	}
	alertmanagerAlertsURL, err := resolveEndpoint(alertmanagerBaseURL, "/api/v2/alerts")
	if err != nil {
		return nil, fmt.Errorf("configure Alertmanager alert endpoint: %w", err)
	}
	alertmanagerEndpoint, err := url.Parse(alertmanagerAlertsURL)
	if err != nil {
		return nil, fmt.Errorf("configure Alertmanager alert filters: %w", err)
	}
	query := alertmanagerEndpoint.Query()
	query.Set("active", "true")
	query.Set("silenced", "true")
	query.Set("inhibited", "true")
	query.Set("unprocessed", "true")
	alertmanagerEndpoint.RawQuery = query.Encode()
	alertmanagerAlertsURL = alertmanagerEndpoint.String()
	httpClient := options.HTTPClient
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 3 * time.Second}
	}
	now := options.Now
	if now == nil {
		now = time.Now
	}
	return &Client{
		prometheusAlertsURL:   prometheusAlertsURL,
		alertmanagerAlertsURL: alertmanagerAlertsURL,
		httpClient:            httpClient,
		now:                   now,
	}, nil
}

func resolveEndpoint(baseURL string, endpointPath string) (string, error) {
	parsed, err := url.Parse(baseURL)
	if err != nil {
		return "", err
	}
	if (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return "", fmt.Errorf("base URL must use http or https and include a host")
	}
	parsed.Path = endpointPath
	parsed.RawPath = ""
	parsed.RawQuery = ""
	parsed.Fragment = ""
	return parsed.String(), nil
}

func (client *Client) Snapshot(ctx context.Context) Snapshot {
	observedAt := client.now().UTC()
	var prometheusResult prometheusResponse
	var alertmanagerResult []alertmanagerAlert
	var prometheusErr error
	var alertmanagerErr error

	var wait sync.WaitGroup
	wait.Add(2)
	go func() {
		defer wait.Done()
		prometheusErr = client.readJSON(ctx, client.prometheusAlertsURL, &prometheusResult)
		if prometheusErr == nil && prometheusResult.Status != "success" {
			prometheusErr = fmt.Errorf("Prometheus returned status %q", prometheusResult.Status)
		}
	}()
	go func() {
		defer wait.Done()
		alertmanagerErr = client.readJSON(ctx, client.alertmanagerAlertsURL, &alertmanagerResult)
	}()
	wait.Wait()

	snapshot := Snapshot{
		Status: SnapshotReady,
		Sources: Sources{
			Prometheus:   SourceReady,
			Alertmanager: SourceReady,
		},
		ObservedAt: observedAt,
	}
	if prometheusErr != nil {
		snapshot.Status = SnapshotDegraded
		snapshot.Sources.Prometheus = SourceUnavailable
	}
	if alertmanagerErr != nil {
		snapshot.Status = SnapshotDegraded
		snapshot.Sources.Alertmanager = SourceUnavailable
	}

	itemsByLabels := make(map[string]Item)
	if prometheusErr == nil {
		for _, alert := range prometheusResult.Data.Alerts {
			item := itemFromPrometheus(alert, observedAt)
			itemsByLabels[canonicalLabels(alert.Labels)] = item
		}
	}
	if alertmanagerErr == nil {
		for _, alert := range alertmanagerResult {
			key := canonicalLabels(alert.Labels)
			item, found := itemsByLabels[key]
			if !found {
				item = itemFromAlertmanager(alert, observedAt)
			} else {
				item.State = stateFromAlertmanager(alert.Status)
				if alert.Fingerprint != "" {
					item.ID = alert.Fingerprint
				}
			}
			itemsByLabels[key] = item
		}
	}

	snapshot.Items = make([]Item, 0, len(itemsByLabels))
	for _, item := range itemsByLabels {
		snapshot.Items = append(snapshot.Items, item)
	}
	sort.Slice(snapshot.Items, func(left int, right int) bool {
		leftItem := snapshot.Items[left]
		rightItem := snapshot.Items[right]
		if severityRank(leftItem.Severity) != severityRank(rightItem.Severity) {
			return severityRank(leftItem.Severity) < severityRank(rightItem.Severity)
		}
		if stateRank(leftItem.State) != stateRank(rightItem.State) {
			return stateRank(leftItem.State) < stateRank(rightItem.State)
		}
		if !leftItem.ActiveAt.Equal(rightItem.ActiveAt) {
			return leftItem.ActiveAt.Before(rightItem.ActiveAt)
		}
		return leftItem.Name < rightItem.Name
	})
	snapshot.Counts.Total = len(snapshot.Items)
	for _, item := range snapshot.Items {
		switch item.State {
		case StatePending:
			snapshot.Counts.Pending++
		case StateFiring:
			snapshot.Counts.Firing++
		case StateSilenced:
			snapshot.Counts.Silenced++
		case StateInhibited:
			snapshot.Counts.Inhibited++
		}
	}
	return snapshot
}

func (client *Client) readJSON(ctx context.Context, endpoint string, target any) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	request.Header.Set("Accept", "application/json")
	response, err := client.httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 8<<10))
		return fmt.Errorf("upstream returned HTTP %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maximumUpstreamBodyBytes+1))
	if err != nil {
		return err
	}
	if len(body) > maximumUpstreamBodyBytes {
		return fmt.Errorf("upstream response exceeds %d bytes", maximumUpstreamBodyBytes)
	}
	if err := json.Unmarshal(body, target); err != nil {
		return fmt.Errorf("decode upstream response: %w", err)
	}
	return nil
}

type prometheusResponse struct {
	Status string `json:"status"`
	Data   struct {
		Alerts []prometheusAlert `json:"alerts"`
	} `json:"data"`
}

type prometheusAlert struct {
	Labels      map[string]string `json:"labels"`
	Annotations map[string]string `json:"annotations"`
	State       string            `json:"state"`
	ActiveAt    time.Time         `json:"activeAt"`
	Value       string            `json:"value"`
}

type alertmanagerAlert struct {
	Labels      map[string]string  `json:"labels"`
	Annotations map[string]string  `json:"annotations"`
	StartsAt    time.Time          `json:"startsAt"`
	Fingerprint string             `json:"fingerprint"`
	Status      alertmanagerStatus `json:"status"`
}

type alertmanagerStatus struct {
	State       string   `json:"state"`
	SilencedBy  []string `json:"silencedBy"`
	InhibitedBy []string `json:"inhibitedBy"`
}

func itemFromPrometheus(alert prometheusAlert, fallbackTime time.Time) Item {
	activeAt := alert.ActiveAt
	if activeAt.IsZero() {
		activeAt = fallbackTime
	}
	value := alert.Value
	return Item{
		ID:          stableAlertID(alert.Labels),
		Name:        alertName(alert.Labels),
		Severity:    severityFromLabels(alert.Labels),
		State:       stateFromPrometheus(alert.State),
		Summary:     alertSummary(alert.Annotations, alert.Labels),
		ActiveAt:    activeAt.UTC(),
		Value:       &value,
		Labels:      copyStringMap(alert.Labels),
		Annotations: copyStringMap(alert.Annotations),
	}
}

func itemFromAlertmanager(alert alertmanagerAlert, fallbackTime time.Time) Item {
	activeAt := alert.StartsAt
	if activeAt.IsZero() {
		activeAt = fallbackTime
	}
	identifier := alert.Fingerprint
	if identifier == "" {
		identifier = stableAlertID(alert.Labels)
	}
	return Item{
		ID:          identifier,
		Name:        alertName(alert.Labels),
		Severity:    severityFromLabels(alert.Labels),
		State:       stateFromAlertmanager(alert.Status),
		Summary:     alertSummary(alert.Annotations, alert.Labels),
		ActiveAt:    activeAt.UTC(),
		Labels:      copyStringMap(alert.Labels),
		Annotations: copyStringMap(alert.Annotations),
	}
}

func stateFromPrometheus(value string) State {
	if strings.EqualFold(value, string(StatePending)) {
		return StatePending
	}
	return StateFiring
}

func stateFromAlertmanager(status alertmanagerStatus) State {
	if len(status.SilencedBy) > 0 {
		return StateSilenced
	}
	if len(status.InhibitedBy) > 0 {
		return StateInhibited
	}
	return StateFiring
}

func severityFromLabels(labels map[string]string) Severity {
	switch strings.ToLower(labels["severity"]) {
	case string(SeverityInfo):
		return SeverityInfo
	case string(SeverityWarning):
		return SeverityWarning
	case string(SeverityCritical):
		return SeverityCritical
	default:
		return SeverityUnknown
	}
}

func alertName(labels map[string]string) string {
	if name := strings.TrimSpace(labels["alertname"]); name != "" {
		return name
	}
	return "UnnamedAlert"
}

func alertSummary(annotations map[string]string, labels map[string]string) string {
	if summary := strings.TrimSpace(annotations["summary"]); summary != "" {
		return summary
	}
	return alertName(labels)
}

func stableAlertID(labels map[string]string) string {
	digest := sha256.Sum256([]byte(canonicalLabels(labels)))
	return hex.EncodeToString(digest[:])
}

func canonicalLabels(labels map[string]string) string {
	keys := make([]string, 0, len(labels))
	for key := range labels {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var result strings.Builder
	for _, key := range keys {
		result.WriteString(key)
		result.WriteByte(0)
		result.WriteString(labels[key])
		result.WriteByte(0xff)
	}
	return result.String()
}

func copyStringMap(source map[string]string) map[string]string {
	result := make(map[string]string, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

func severityRank(severity Severity) int {
	switch severity {
	case SeverityCritical:
		return 0
	case SeverityWarning:
		return 1
	case SeverityInfo:
		return 2
	default:
		return 3
	}
}

func stateRank(state State) int {
	switch state {
	case StateFiring:
		return 0
	case StatePending:
		return 1
	case StateInhibited:
		return 2
	default:
		return 3
	}
}

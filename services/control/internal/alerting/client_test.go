package alerting

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestClientMergesPrometheusRulesWithAlertmanagerSuppression(t *testing.T) {
	prometheus := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/v1/alerts" {
			t.Fatalf("unexpected Prometheus path %q", request.URL.Path)
		}
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{
  "status": "success",
  "data": {"alerts": [
    {
      "labels": {"alertname": "RainPulseRadarDataStale", "severity": "warning", "radar_id": "z9598"},
      "annotations": {"summary": "Radar data is stale"},
      "state": "firing",
      "activeAt": "2026-08-30T06:55:00Z",
      "value": "901"
    },
    {
      "labels": {"alertname": "RainPulseRadarQualityLow", "severity": "critical", "radar_id": "z9598"},
      "annotations": {"summary": "Radar quality is low"},
      "state": "pending",
      "activeAt": "2026-08-30T06:59:00Z",
      "value": "0.41"
    }
  ]}
}`))
	}))
	defer prometheus.Close()

	alertmanager := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/v2/alerts" {
			t.Fatalf("unexpected Alertmanager path %q", request.URL.Path)
		}
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`[{
  "annotations": {"summary": "Radar data is stale"},
  "labels": {"alertname": "RainPulseRadarDataStale", "severity": "warning", "radar_id": "z9598"},
  "startsAt": "2026-08-30T06:55:00Z",
  "fingerprint": "am-fingerprint",
  "status": {"state": "suppressed", "silencedBy": ["maintenance-window"], "inhibitedBy": []}
}]`))
	}))
	defer alertmanager.Close()

	client, err := NewClient(prometheus.URL, alertmanager.URL, Options{
		HTTPClient: http.DefaultClient,
		Now: func() time.Time {
			return time.Date(2026, 8, 30, 7, 0, 0, 0, time.UTC)
		},
	})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	snapshot := client.Snapshot(context.Background())
	if snapshot.Status != SnapshotReady || snapshot.Sources.Prometheus != SourceReady || snapshot.Sources.Alertmanager != SourceReady {
		t.Fatalf("unexpected source status: %+v", snapshot)
	}
	if snapshot.Counts.Total != 2 || snapshot.Counts.Pending != 1 || snapshot.Counts.Silenced != 1 || snapshot.Counts.Firing != 0 {
		t.Fatalf("unexpected counts: %+v", snapshot.Counts)
	}
	if len(snapshot.Items) != 2 {
		t.Fatalf("expected 2 alerts, got %d", len(snapshot.Items))
	}
	if snapshot.Items[0].Name != "RainPulseRadarQualityLow" || snapshot.Items[0].State != StatePending {
		t.Fatalf("critical pending alert should sort first: %+v", snapshot.Items)
	}
	if snapshot.Items[1].ID != "am-fingerprint" || snapshot.Items[1].State != StateSilenced {
		t.Fatalf("Alertmanager suppression was not merged: %+v", snapshot.Items[1])
	}
}

func TestClientKeepsPrometheusEvidenceWhenAlertmanagerIsUnavailable(t *testing.T) {
	prometheus := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{
  "status": "success",
  "data": {"alerts": [{
    "labels": {"alertname": "RainPulseJobStuck", "severity": "critical"},
    "annotations": {"summary": "Job remains stuck"},
    "state": "firing",
    "activeAt": "2026-08-30T06:50:00Z",
    "value": "1200"
  }]}
}`))
	}))
	defer prometheus.Close()

	alertmanager := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		http.Error(response, "unavailable", http.StatusServiceUnavailable)
	}))
	defer alertmanager.Close()

	client, err := NewClient(prometheus.URL, alertmanager.URL, Options{HTTPClient: http.DefaultClient})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}
	snapshot := client.Snapshot(context.Background())

	if snapshot.Status != SnapshotDegraded || snapshot.Sources.Prometheus != SourceReady || snapshot.Sources.Alertmanager != SourceUnavailable {
		t.Fatalf("unexpected degraded status: %+v", snapshot)
	}
	if snapshot.Counts.Firing != 1 || len(snapshot.Items) != 1 {
		t.Fatalf("Prometheus evidence should remain visible: %+v", snapshot)
	}
}

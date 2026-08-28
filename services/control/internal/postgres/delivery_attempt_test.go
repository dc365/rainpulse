package postgres

import (
	"encoding/json"
	"testing"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
)

func TestCompletionDeliveryAttemptUsesWorkerDiagnostics(t *testing.T) {
	event := orchestration.JobCompleted{Payload: orchestration.JobCompletedPayload{
		Diagnostics: map[string]json.RawMessage{
			"worker_delivery": json.RawMessage(`{"attempt":3,"max_deliveries":3}`),
		},
	}}
	if got := completionDeliveryAttempt(event); got != 3 {
		t.Fatalf("attempt = %d, want 3", got)
	}
}

func TestFailureDeliveryAttemptUsesFailureDetails(t *testing.T) {
	event := orchestration.JobFailed{Payload: orchestration.JobFailedPayload{
		Details: map[string]any{"delivery_attempt": float64(2)},
	}}
	if got := failureDeliveryAttempt(event); got != 2 {
		t.Fatalf("attempt = %d, want 2", got)
	}
}

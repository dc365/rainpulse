package webgateway

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAdministrativeMutationRecognizesBoundedCancellation(t *testing.T) {
	request := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/admin/regenerations/82600000-0000-4000-8000-000000000001/cancel",
		nil,
	)
	if !isAdministrativeMutation(request) {
		t.Fatal("cancellation route was not recognized")
	}
	for _, path := range []string{
		"/api/v1/admin/regenerations/not-a-uuid/cancel",
		"/api/v1/admin/regenerations/82600000-0000-4000-8000-000000000001/other",
	} {
		candidate := httptest.NewRequest(http.MethodPost, path, nil)
		if isAdministrativeMutation(candidate) {
			t.Fatalf("unexpected administrative route: %s", path)
		}
	}
}

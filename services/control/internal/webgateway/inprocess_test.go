package webgateway

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestInProcessAPIKeepsAdminBoundary(t *testing.T) {
	calls := 0
	h, err := NewHandler(Options{APIHandler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path != "/api/ping" && r.Header.Get("Authorization") != "Bearer private" {
			t.Error("missing server token")
		}
		w.WriteHeader(204)
	}), AdminToken: "private"})
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		path string
		want int
	}{
		{"/api/ping", 204},
		{"/api/v1/admin/secrets", 404},
		{"/api/v1/admin/runs/00000000-0000-0000-0000-000000000001/rerun", 204},
	} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", test.path, nil))
		if w.Code != test.want {
			t.Fatalf("%s: %d", test.path, w.Code)
		}
	}
	if calls != 2 {
		t.Fatalf("calls %d", calls)
	}
}

func TestQCBatchRoutes(t *testing.T) {
	for _, method := range []string{"GET", "POST"} {
		h, err := NewHandler(Options{APIHandler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method == "POST" && r.Header.Get("Authorization") != "Bearer private" {
				t.Error("missing token")
			}
			w.WriteHeader(204)
		}), AdminToken: "private"})
		if err != nil {
			t.Fatal(err)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest(method, "/api/v1/admin/qc-batches?date=2026-08-28", nil))
		if w.Code != 204 {
			t.Fatalf("%s %d", method, w.Code)
		}
	}
}

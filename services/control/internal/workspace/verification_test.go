package workspace

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSpatialVerificationRejectsInvalidRequests(t *testing.T) {
	h := &runtimeHandler{}
	for _, body := range []string{`{}`, `{"cycle_id":"x","algorithm":"lk","lead_minutes":0}`, `{"cycle_id":"x","algorithm":"qpe","lead_minutes":5}`, `{"cycle_id":"x","algorithm":"lk","lead_minutes":5,"uri":"private"}`} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", verificationPath, strings.NewReader(body)))
		if w.Code != 400 {
			t.Fatalf("got %d for %s", w.Code, body)
		}
	}
}

func TestNativeVerificationRejectsDerivedAndReference(t *testing.T) {
	p := panelView{Frames: []frameView{{LeadMinutes: 10, FrameKind: "derived"}}}
	if hasNativeLead(p, 10, false) {
		t.Fatal("derived allowed")
	}
	p.Frames[0].FrameKind = "native"
	if !hasNativeLead(p, 10, false) {
		t.Fatal("native rejected")
	}
}

package operations

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func identity(kind string) Identity {
	return Identity{Kind: kind, Fingerprint: strings.Repeat("a", 64), CodeSHA256: strings.Repeat("b", 64), Files: map[string]string{"cfg": strings.Repeat("c", 64)}, Versions: map[string]string{"version": "v1"}}
}
func TestRunStatusAndActions(t *testing.T) {
	cases := []struct {
		mode string
		c    map[string]int
		want string
	}{
		{"ACTIVE", map[string]int{"SUCCEEDED": 2}, "SUCCEEDED"}, {"ACTIVE", map[string]int{"SUCCEEDED": 1, "FAILED": 1}, "PARTIAL_SUCCESS"}, {"ACTIVE", map[string]int{"BLOCKED": 2}, "FAILED"}, {"ACTIVE", map[string]int{"RUNNING": 1, "FAILED": 2}, "RUNNING"}, {"ACTIVE", map[string]int{"WAITING": 1}, "QUEUED"}, {"PAUSED", map[string]int{"RUNNING": 1}, "PAUSED"}, {"CANCELLED", map[string]int{"COMMITTING": 1}, "CANCELLING"}, {"CANCELLED", map[string]int{"SUCCEEDED": 1}, "CANCELLED"}}
	for _, c := range cases {
		t.Run(c.want+c.mode, func(t *testing.T) {
			if got := Aggregate(c.mode, c.c); got != c.want {
				t.Fatalf("%s", got)
			}
		})
	}
	if len(AllowedActions("CANCELLED", "CANCELLED")) != 0 {
		t.Fatal("cancelled run permits retry")
	}
	if len(AllowedActions("ACTIVE", "SUCCEEDED")) != 0 {
		t.Fatal("successful run permits in-place overwrite")
	}
}
func TestPlanValidation(t *testing.T) {
	now := time.Now().UTC()
	root := NewID()
	child := NewID()
	p := Plan{ID: NewID(), RunID: NewID(), Tasks: []Spec{{ID: root, Kind: "qc", Request: json.RawMessage(`{"payload":{}}`)}, {ID: child, ParentID: root, Kind: "render", Request: json.RawMessage(`{"payload":{}}`)}}}
	if err := p.Seal(now); err != nil || !p.Submittable {
		t.Fatal(err)
	}
	hash := p.Digest
	p.Digest = ""
	if hash != CanonicalDigest(p) {
		t.Fatal("nonreproducible seal")
	}
	if p.ExpiresAt.Sub(p.CreatedAt) != 15*time.Minute {
		t.Fatal("wrong expiry")
	}
	p.Tasks[0].ParentID = child
	if p.Seal(now) == nil {
		t.Fatal("cycle accepted")
	}
	p.Tasks = nil
	if p.Seal(now) != nil || p.Submittable {
		t.Fatal("empty plan accepted")
	}
}
func TestSelection(t *testing.T) {
	now := time.Now().UTC()
	valid := Selection{Preset: "qc_preview", Start: now.Add(-time.Hour), End: now, RadarIDs: []string{"z9591"}}
	if valid.Validate() != nil {
		t.Fatal("valid rejected")
	}
	for _, change := range []func(*Selection){func(s *Selection) { s.End = s.Start }, func(s *Selection) { s.End = s.Start.Add(25 * time.Hour) }, func(s *Selection) { s.RadarIDs = []string{"A", "a"} }, func(s *Selection) { s.RadarIDs = []string{"../a"} }, func(s *Selection) { s.Preset = "execute_shell" }} {
		v := valid
		change(&v)
		if v.Validate() == nil {
			t.Fatal("invalid accepted")
		}
	}
}
func TestWorkerMatching(t *testing.T) {
	now := time.Now().UTC()
	w := WorkerInfo{ID: "w1", Identity: identity("qc"), SeenAt: now, Ready: true}
	expected := Identity{Kind: "qc", Files: w.Identity.Files, Versions: w.Identity.Versions}
	if _, e := MatchWorker(expected, []WorkerInfo{w}, now); e != nil {
		t.Fatal(e)
	}
	duplicate := w
	duplicate.ID = "w2"
	if _, e := MatchWorker(expected, []WorkerInfo{w, duplicate}, now); e != nil {
		t.Fatal("replicas rejected", e)
	}
	duplicate.Identity.Fingerprint = strings.Repeat("f", 64)
	if _, e := MatchWorker(expected, []WorkerInfo{w, duplicate}, now); e == nil {
		t.Fatal("ambiguous code versions accepted")
	}
	w.SeenAt = now.Add(-2 * time.Minute)
	if _, e := MatchWorker(expected, []WorkerInfo{w}, now); e == nil {
		t.Fatal("stale worker accepted")
	}
}
func TestPulseRejectsUnknownsAndExcessLogs(t *testing.T) {
	p := Pulse{TaskID: NewID(), AttemptID: NewID(), Token: strings.Repeat("a", 64), Stage: "COMPUTE", Metrics: map[string]float64{"context_ms": 3}}
	if p.Validate() != nil {
		t.Fatal("valid rejected")
	}
	p.Stage = "99percent"
	if p.Validate() == nil {
		t.Fatal("fake stage accepted")
	}
	p.Stage = "COMPUTE"
	p.Lines = make([]LogLine, 21)
	if p.Validate() == nil {
		t.Fatal("unbounded logs accepted")
	}
}
func TestAuthenticationAndUnknownRoutes(t *testing.T) {
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) })
	h := NewHandler(nil, next, HTTPOptions{AdminToken: "admin", WorkerToken: "worker"})
	for _, tc := range []struct {
		path, token string
		status      int
	}{{"/api/v1/admin/ops/runs", "", 401}, {"/internal/ops/v1/claim", "admin", 401}, {"/api/v1/admin/ops/unknown", "worker", 401}, {"/api/v1/admin/ops/unknown", "admin", 404}, {"/api/v1/workspace/cycles", "", 204}} {
		r := httptest.NewRequest("GET", tc.path, nil)
		if tc.token != "" {
			r.Header.Set("Authorization", "Bearer "+tc.token)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != tc.status {
			t.Fatalf("%s: %d", tc.path, w.Code)
		}
	}
}

func TestValidationModeDisablesOnlyAdminCredentialChecks(t *testing.T) {
	next := http.NotFoundHandler()
	h := NewHandler(nil, next, HTTPOptions{AdminToken: "admin", WorkerToken: "worker", AdminAuthMode: AdminAuthModeValidation})

	modeRequest := httptest.NewRequest(http.MethodGet, "/api/v1/admin/ops/auth-mode", nil)
	modeResponse := httptest.NewRecorder()
	h.ServeHTTP(modeResponse, modeRequest)
	if modeResponse.Code != http.StatusOK {
		t.Fatalf("auth mode endpoint: %d", modeResponse.Code)
	}
	var mode map[string]string
	if err := json.Unmarshal(modeResponse.Body.Bytes(), &mode); err != nil || mode["mode"] != AdminAuthModeValidation {
		t.Fatalf("unexpected auth mode response: %s (%v)", modeResponse.Body.String(), err)
	}

	adminRequest := httptest.NewRequest(http.MethodGet, "/api/v1/admin/ops/unknown", nil)
	adminResponse := httptest.NewRecorder()
	h.ServeHTTP(adminResponse, adminRequest)
	if adminResponse.Code != http.StatusNotFound {
		t.Fatalf("validation admin request should pass auth and reach routing: %d", adminResponse.Code)
	}

	workerRequest := httptest.NewRequest(http.MethodGet, "/internal/ops/v1/unknown", nil)
	workerResponse := httptest.NewRecorder()
	h.ServeHTTP(workerResponse, workerRequest)
	if workerResponse.Code != http.StatusUnauthorized {
		t.Fatalf("validation mode must preserve worker auth: %d", workerResponse.Code)
	}
	workerRequest = httptest.NewRequest(http.MethodGet, "/internal/ops/v1/unknown", nil)
	workerRequest.Header.Set("Authorization", "Bearer admin")
	workerResponse = httptest.NewRecorder()
	h.ServeHTTP(workerResponse, workerRequest)
	if workerResponse.Code != http.StatusUnauthorized {
		t.Fatalf("admin token must not authorize worker routes: %d", workerResponse.Code)
	}
}

func TestAdminAuthModeDefaultsToCredential(t *testing.T) {
	h := NewHandler(nil, http.NotFoundHandler(), HTTPOptions{AdminToken: "admin"})
	modeRequest := httptest.NewRequest(http.MethodGet, "/api/v1/admin/ops/auth-mode", nil)
	modeResponse := httptest.NewRecorder()
	h.ServeHTTP(modeResponse, modeRequest)
	var mode map[string]string
	if err := json.Unmarshal(modeResponse.Body.Bytes(), &mode); err != nil || mode["mode"] != "credential" {
		t.Fatalf("unexpected default auth mode response: %s (%v)", modeResponse.Body.String(), err)
	}
	adminRequest := httptest.NewRequest(http.MethodGet, "/api/v1/admin/ops/unknown", nil)
	adminResponse := httptest.NewRecorder()
	h.ServeHTTP(adminResponse, adminRequest)
	if adminResponse.Code != http.StatusUnauthorized {
		t.Fatalf("credential mode should continue requiring admin auth: %d", adminResponse.Code)
	}
}

func TestAdmissionPauseAndTelemetryBoundary(t *testing.T) {
	calls := 0
	h := NewHandler(nil, http.NotFoundHandler(), HTTPOptions{AdminToken: "a", WorkerToken: "w", Admit: func(context.Context) (func(), error) { calls++; return nil, errors.New("paused") }})
	r := httptest.NewRequest("POST", "/api/v1/admin/ops/plans", strings.NewReader(`{}`))
	r.Header.Set("Authorization", "Bearer a")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 503 || calls != 1 {
		t.Fatal(w.Code, calls)
	}
	r = httptest.NewRequest("POST", "/internal/ops/v1/unknown", strings.NewReader(`{}`))
	r.Header.Set("Authorization", "Bearer w")
	w = httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 404 || calls != 1 {
		t.Fatal("telemetry entered admission guard")
	}
}
func TestStrictBodies(t *testing.T) {
	for _, body := range []string{`{"extra":true}`, `{} {}`, strings.Repeat("x", 1024*1024+1)} {
		r := httptest.NewRequest("POST", "/", strings.NewReader(body))
		if decodeBody(r, &struct{}{}) == nil {
			t.Fatal("invalid body accepted")
		}
	}
	r := httptest.NewRequest("POST", "/", strings.NewReader(`{}`))
	if decodeBody(r, &struct{}{}) != nil {
		t.Fatal("valid body rejected")
	}
}
func TestCursorFilters(t *testing.T) {
	at := time.Now().UTC()
	id := NewID()
	raw := encodeCursor(at, id, "scope")
	got, gid, e := decodeCursor(raw, "scope")
	if e != nil || !got.Equal(at) || gid != id {
		t.Fatal(e)
	}
	if _, _, e = decodeCursor(raw, "other"); e == nil {
		t.Fatal("cross-scope cursor accepted")
	}
}
func TestRedaction(t *testing.T) {
	text := Redact(`password=oops Bearer abcDEF123 https://admin:private@host/path token="private"`)
	for _, s := range []string{"oops", "abcDEF123", "admin:private", "token=\"private"} {
		if strings.Contains(text, s) {
			t.Fatal(text)
		}
	}
	raw := RedactJSON([]byte(`{"request":{"token":"value","n":1},"message":"password=hidden"}`))
	if !json.Valid(raw) || strings.Contains(string(raw), "hidden") || strings.Contains(string(raw), "value") {
		t.Fatal(string(raw))
	}
}
func TestLogsAbsentIsNotEmptySuccess(t *testing.T) {
	v, e := ReadSystemLogs(context.Background(), "", NewID(), "", "")
	if e != nil || v.(map[string]any)["status"] != "not_configured" {
		t.Fatal(v, e)
	}
}
func TestLogsQueryFixedAndBounded(t *testing.T) {
	id := NewID()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("query") != `{app="rainpulse"} | json | job_id="`+id+`"` || r.URL.Query().Get("limit") != "200" {
			t.Fatal(r.URL)
		}
		io.WriteString(w, `{"status":"success","data":{"result":[{"values":[["123","token=secret"]]}]}}`)
	}))
	defer server.Close()
	v, e := ReadSystemLogs(context.Background(), server.URL, id, "", "")
	if e != nil {
		t.Fatal(e)
	}
	if strings.Contains(string(JSON(v)), "token=secret") {
		t.Fatal("unredacted log")
	}
	if _, e = ReadSystemLogs(context.Background(), server.URL, `x" |drop`, "", ""); e == nil {
		t.Fatal("query injection accepted")
	}
}

func TestPlanDigestSurvivesJSONBKeyOrder(t *testing.T) {
	p := Plan{ID: NewID(), RunID: NewID(), Tasks: []Spec{{ID: NewID(), Kind: "qc", Request: json.RawMessage(`{"payload":{"b":2,"a":1},"event_type":"request"}`)}}}
	if err := p.Seal(time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	original := p.Digest
	p.Digest = ""
	p.Tasks[0].Request = json.RawMessage(`{ "event_type": "request", "payload": {"a": 1, "b": 2} }`)
	if CanonicalDigest(p) != original {
		t.Fatal("semantically identical jsonb reordering invalidates plan")
	}
	p.Tasks[0].Request = json.RawMessage(`{"event_type":"request","payload":{"a":9,"b":2}}`)
	if CanonicalDigest(p) == original {
		t.Fatal("modified payload preserved plan digest")
	}
}

func TestEmptyBlockedPlanUsesArrays(t *testing.T) {
	p := Plan{ID: NewID(), RunID: NewID()}
	if err := p.Seal(time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	if p.Submittable {
		t.Fatal("empty plan is submittable")
	}
	var wire map[string]any
	if err := json.Unmarshal(JSON(p), &wire); err != nil {
		t.Fatal(err)
	}
	if _, ok := wire["tasks"].([]any); !ok {
		t.Fatal("blocked tasks must be an array, not null")
	}
	if _, ok := wire["checks"].([]any); !ok {
		t.Fatal("checks must be an array")
	}
}

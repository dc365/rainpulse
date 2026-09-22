package operations

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

type fakeObjects struct {
	raw []byte
	uri string
}

func (f *fakeObjects) ReadObject(_ context.Context, u string, n int64) ([]byte, string, error) {
	f.uri = u
	return f.raw, "", nil
}
func fixtureMarker() (Marker, AssetRef, []byte) {
	id, run, trace := NewID(), NewID(), NewID()
	now := time.Now().UTC()
	uri := "s3://rainpulse/operations/" + run + "/" + id + "/attempts/" + NewID() + "/qc.zarr"
	e := JSON(map[string]any{"event_id": NewID(), "event_type": "job.completed", "job_id": id, "run_id": run, "trace_id": trace, "payload": map[string]any{"status": "succeeded", "started_at": now, "finished_at": now.Add(time.Second), "runtime_ms": 1000, "assets": []any{map[string]any{"uri": uri, "sha256": strings.Repeat("a", 64), "size_bytes": 3}}, "diagnostics": map[string]any{"operational_eligible": false}}})
	m := Marker{SchemaVersion: "2.0", SHA256: strings.Repeat("a", 64), SizeBytes: 3, DataPrefix: "_objects/" + strings.Repeat("a", 64), Objects: []ObjectEntry{{"x.json", Digest([]byte(`{}`)), 3}}, Completion: e}
	ref := AssetRef{URI: uri, SHA256: m.SHA256, SizeBytes: 3, MarkerSHA256: Digest(JSON(m))}
	request := JSON(map[string]any{"job_id": id, "run_id": run, "trace_id": trace, "payload": map[string]any{"output_prefix": strings.TrimSuffix(uri, "qc.zarr")}})
	return m, ref, request
}
func TestCandidateBinding(t *testing.T) {
	m, ref, req := fixtureMarker()
	c, e := VerifyCandidate(req, m, ref)
	if e != nil || !c.CandidateOnly {
		t.Fatal(c, e)
	}
	bad := ref
	bad.URI += "/other"
	if _, e = VerifyCandidate(req, m, bad); e == nil {
		t.Fatal("wrong URI accepted")
	}
	var r map[string]any
	json.Unmarshal(req, &r)
	r["job_id"] = NewID()
	if _, e = VerifyCandidate(JSON(r), m, ref); e == nil {
		t.Fatal("old attempt accepted")
	}
}
func TestMarkerValidation(t *testing.T) {
	m, _, _ := fixtureMarker()
	if _, e := ParseMarker(JSON(m)); e != nil {
		t.Fatal(e)
	}
	for _, change := range []func(*Marker){func(v *Marker) { v.SizeBytes++ }, func(v *Marker) { v.Objects = append(v.Objects, v.Objects[0]); v.SizeBytes *= 2 }, func(v *Marker) { v.DataPrefix = "../x" }, func(v *Marker) { v.SchemaVersion = "99" }, func(v *Marker) { v.Objects[0].Key = "../x" }} {
		v := m
		v.Objects = append([]ObjectEntry(nil), m.Objects...)
		change(&v)
		if _, e := ParseMarker(JSON(v)); e == nil {
			t.Fatal("bad marker accepted")
		}
	}
}
func TestProbeAndPrefixRestrictions(t *testing.T) {
	m, ref, request := fixtureMarker()
	obj := &fakeObjects{raw: JSON(m)}
	s := Service{Objects: obj}
	r, e := s.Probe(context.Background(), ref.URI)
	if e != nil || r.MarkerSHA256 != Digest(obj.raw) || !strings.HasSuffix(obj.uri, "/_SUCCESS.json") {
		t.Fatal(r, e)
	}
	if _, e = s.candidate(context.Background(), request, "qc"); e != nil {
		t.Fatal(e)
	}
	for _, uri := range []string{"file:///etc/passwd", "s3://bucket/../other", "s3://user:pass@bucket/a", "https://host/x"} {
		if _, e = s.Probe(context.Background(), uri); e == nil {
			t.Fatal("unsafe URI", uri)
		}
	}
}

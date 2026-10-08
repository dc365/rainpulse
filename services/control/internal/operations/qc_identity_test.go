package operations

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func qcMarkerFixture(t *testing.T) (Marker, AssetRef) {
	t.Helper()
	m, ref, _ := fixtureMarker()
	var event map[string]any
	if err := json.Unmarshal(m.Completion, &event); err != nil {
		t.Fatal(err)
	}
	payload := event["payload"].(map[string]any)
	payload["diagnostics"] = map[string]any{"radar_qc": map[string]any{
		"radar_id": "s1", "scan_id": NewID(), "qc_pipeline_version": "stored-pipeline",
		"qc_profile": "stored-profile", "parameters_hash": strings.Repeat("b", 64),
		"implementation_revision": "python-source-tree-v1:" + strings.Repeat("c", 64),
		"libraries":               map[string]string{"wradlib": "stored-version"},
	}}
	payload["assets"].([]any)[0].(map[string]any)["asset_type"] = "qc_radar_volume"
	m.Completion = JSON(event)
	return m, ref
}

func mutateQCMarker(t *testing.T, m Marker, change func(map[string]any)) Marker {
	t.Helper()
	var event map[string]any
	if err := json.Unmarshal(m.Completion, &event); err != nil {
		t.Fatal(err)
	}
	change(event["payload"].(map[string]any))
	m.Completion = JSON(event)
	return m
}

func TestProbeFreezesDeclaredQCIdentityForPackedAndUnpackedAssets(t *testing.T) {
	for _, schema := range []string{"2.0", "3.0"} {
		m, ref := qcMarkerFixture(t)
		m.SchemaVersion = schema
		s := Service{Objects: &fakeObjects{raw: JSON(m)}}
		actual, err := s.Probe(context.Background(), ref.URI)
		if err != nil || actual.QCIdentity == nil {
			t.Fatal(actual, err)
		}
		identity := actual.QCIdentity
		if identity.RadarID != "s1" || identity.PipelineVersion != "stored-pipeline" || identity.ParametersSHA256 != strings.Repeat("b", 64) || identity.ImplementationRevision != "python-source-tree-v1:"+strings.Repeat("c", 64) || identity.Libraries["wradlib"] != "stored-version" {
			t.Fatal("substituted or lost producer declaration", identity)
		}
	}
}

func TestLegacyQCIdentityKeepsUnreportedRevisionAndLibrariesUnknown(t *testing.T) {
	m, ref := qcMarkerFixture(t)
	m = mutateQCMarker(t, m, func(payload map[string]any) {
		qc := payload["diagnostics"].(map[string]any)["radar_qc"].(map[string]any)
		delete(qc, "implementation_revision")
		delete(qc, "libraries")
	})
	s := Service{Objects: &fakeObjects{raw: JSON(m)}}
	actual, err := s.Probe(context.Background(), ref.URI)
	if err != nil || actual.QCIdentity == nil || actual.QCIdentity.ImplementationRevision != "" || actual.QCIdentity.Libraries != nil {
		t.Fatal(actual, err)
	}
}

func TestQCIdentityCannotBeBorrowedFromAnotherAssetOrInvalidDeclaration(t *testing.T) {
	changes := map[string]func(map[string]any){
		"uri":  func(p map[string]any) { p["assets"].([]any)[0].(map[string]any)["uri"] = "s3://other/asset" },
		"sha":  func(p map[string]any) { p["assets"].([]any)[0].(map[string]any)["sha256"] = strings.Repeat("d", 64) },
		"size": func(p map[string]any) { p["assets"].([]any)[0].(map[string]any)["size_bytes"] = 4 },
		"type": func(p map[string]any) {
			p["assets"].([]any)[0].(map[string]any)["asset_type"] = "normalized_radar_volume"
		},
		"failed": func(p map[string]any) { p["status"] = "failed" },
		"parameters": func(p map[string]any) {
			p["diagnostics"].(map[string]any)["radar_qc"].(map[string]any)["parameters_hash"] = "not-sha"
		},
		"revision": func(p map[string]any) {
			p["diagnostics"].(map[string]any)["radar_qc"].(map[string]any)["implementation_revision"] = "current-head"
		},
		"libraries": func(p map[string]any) {
			p["diagnostics"].(map[string]any)["radar_qc"].(map[string]any)["libraries"] = map[string]string{"numpy": ""}
		},
		"duplicate": func(p map[string]any) { a := p["assets"].([]any); p["assets"] = append(a, a[0]) },
	}
	for name, change := range changes {
		t.Run(name, func(t *testing.T) {
			m, ref := qcMarkerFixture(t)
			m = mutateQCMarker(t, m, change)
			s := Service{Objects: &fakeObjects{raw: JSON(m)}}
			if _, err := s.Probe(context.Background(), ref.URI); err == nil {
				t.Fatal("invalid producer declaration accepted")
			}
		})
	}
}

func TestOrdinaryInputsHaveNoManufacturedQCIdentity(t *testing.T) {
	m, ref, _ := fixtureMarker()
	s := Service{Objects: &fakeObjects{raw: JSON(m)}}
	actual, err := s.Probe(context.Background(), ref.URI)
	if err != nil || actual.QCIdentity != nil {
		t.Fatal(actual, err)
	}
}

func TestReadOnlyRealQCMarkerIdentity(t *testing.T) {
	file := os.Getenv("RAINPULSE_TEST_QC_MARKER_RECEIPT")
	if file == "" {
		t.Skip("requires private verified real QC marker receipt")
	}
	raw, err := os.ReadFile(file)
	if err != nil {
		t.Fatal("real receipt unreadable")
	}
	var receipt struct {
		URI        string          `json:"uri"`
		Marker     json.RawMessage `json:"marker"`
		Pipeline   string          `json:"pipeline_version"`
		Parameters string          `json:"parameters_sha256"`
	}
	if json.Unmarshal(raw, &receipt) != nil {
		t.Fatal("real receipt invalid")
	}
	s := Service{Objects: &fakeObjects{raw: receipt.Marker}}
	ref, err := s.Probe(context.Background(), receipt.URI)
	if err != nil {
		t.Fatal("real marker probe rejected", err)
	}
	if ref.QCIdentity == nil || ref.QCIdentity.PipelineVersion != receipt.Pipeline || ref.QCIdentity.ParametersSHA256 != receipt.Parameters {
		t.Fatal("real marker and verified attributes disagree")
	}
	t.Logf("real QC producer declaration recorded; implementation reported=%t; libraries reported=%t", ref.QCIdentity.ImplementationRevision != "", len(ref.QCIdentity.Libraries) > 0)
}

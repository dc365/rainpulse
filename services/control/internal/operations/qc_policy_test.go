package operations

import (
	"encoding/json"
	"strings"
	"testing"
)

func policyFixture() ([]Spec, map[string]AssetRef) {
	identity := &UpstreamQCIdentity{RadarID: "s1", ScanID: NewID(), PipelineVersion: "stored", Profile: "actual", ParametersSHA256: strings.Repeat("a", 64), ImplementationRevision: "python-source-tree-v1:" + strings.Repeat("b", 64), Libraries: map[string]string{"wradlib": "v"}}
	refs := map[string]AssetRef{"s3://bucket/first": {QCIdentity: identity}}
	request := func(sources []any) json.RawMessage {
		return JSON(map[string]any{"payload": map[string]any{"mode": "sx_composite", "requested_s_radars": []string{"s1"}, "sources": sources}})
	}
	return []Spec{{ID: "first", Request: request([]any{map[string]any{"radar_id": "s1", "scan_id": identity.ScanID, "input_uri": "s3://bucket/first"}})}, {ID: "missing", Request: request([]any{})}}, refs
}

func TestFrozenSQCPolicySurvivesAMissingStationFrame(t *testing.T) {
	specs, refs := policyFixture()
	checks := freezeSQCPolicy(specs, refs)
	for _, check := range checks {
		if check.State == "BLOCK" {
			t.Fatal(check)
		}
	}
	var policies []json.RawMessage
	for _, spec := range specs {
		var request struct {
			Payload struct {
				Policy   json.RawMessage  `json:"s_qc_policy"`
				Complete bool             `json:"s_qc_policy_complete"`
				Sources  []map[string]any `json:"sources"`
			} `json:"payload"`
		}
		if err := json.Unmarshal(spec.Request, &request); err != nil {
			t.Fatal(err)
		}
		if !request.Payload.Complete {
			t.Fatal("lost declared policy")
		}
		policies = append(policies, request.Payload.Policy)
		if spec.ID == "first" && request.Payload.Sources[0]["expected_qc_identity"] == nil {
			t.Fatal("missing actual declaration")
		}
	}
	if string(policies[0]) != string(policies[1]) {
		t.Fatal("missing input changed frozen recipe")
	}
}

func TestDifferentDeclaredSQCRecipesBlockWholePreflight(t *testing.T) {
	specs, refs := policyFixture()
	changed := *refs["s3://bucket/first"].QCIdentity
	changed.ScanID = NewID()
	changed.ParametersSHA256 = strings.Repeat("c", 64)
	refs["s3://bucket/second"] = AssetRef{QCIdentity: &changed}
	specs[1].Request = JSON(map[string]any{"payload": map[string]any{"mode": "sx_composite", "requested_s_radars": []string{"s1"}, "sources": []any{map[string]any{"radar_id": "s1", "scan_id": changed.ScanID, "input_uri": "s3://bucket/second"}}}})
	blocked := false
	for _, check := range freezeSQCPolicy(specs, refs) {
		blocked = blocked || check.State == "BLOCK"
	}
	if !blocked {
		t.Fatal("different upstream QC versions silently merged")
	}
}

func TestUnknownUpstreamIdentityAndWhollyMissingStationStayIncomplete(t *testing.T) {
	for _, change := range []func([]Spec, map[string]AssetRef){
		func(_ []Spec, refs map[string]AssetRef) { delete(refs, "s3://bucket/first") },
		func(_ []Spec, refs map[string]AssetRef) {
			refs["s3://bucket/first"].QCIdentity.ImplementationRevision = ""
		},
		func(specs []Spec, _ map[string]AssetRef) {
			specs[1].Request = JSON(map[string]any{"payload": map[string]any{"mode": "sx_composite", "requested_s_radars": []string{"s1", "s2"}, "sources": []any{}}})
		},
	} {
		specs, refs := policyFixture()
		change(specs, refs)
		freezeSQCPolicy(specs, refs)
		for _, spec := range specs {
			var r map[string]any
			json.Unmarshal(spec.Request, &r)
			if r["payload"].(map[string]any)["s_qc_policy_complete"] == true {
				t.Fatal("manufactured complete identity")
			}
		}
	}
}

func TestDifferentSourceScanCannotBorrowDeclaration(t *testing.T) {
	specs, refs := policyFixture()
	refs["s3://bucket/first"].QCIdentity.ScanID = NewID()
	blocked := false
	for _, check := range freezeSQCPolicy(specs, refs) {
		blocked = blocked || check.State == "BLOCK"
	}
	if !blocked {
		t.Fatal("QC declaration used for another scan")
	}
}

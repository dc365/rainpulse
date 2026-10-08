package controlplane

import (
	"testing"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/operations"
)

func TestMultiBandPolicyWorkerSelection(t *testing.T) {
	now := time.Now().UTC()
	identity := operations.Identity{Kind: "multiband", Versions: map[string]string{
		"multiband_contract": "rainpulse.multiband.v1", "network_release": "fixture",
		"execution_policy_sha256": "policy",
	}, Fingerprint: "old"}
	old := operations.WorkerInfo{ID: "old", Identity: identity, Ready: true, SeenAt: now}
	expected := operations.Identity{Kind: "multiband", Versions: multiBandVersions("fixture", "policy", "sx_composite")}
	if _, err := operations.MatchWorker(expected, []operations.WorkerInfo{old}, now); err == nil {
		t.Fatal("new composite matched legacy worker")
	}
	current := old
	current.ID = "new"
	current.Identity.Versions = multiBandVersions("fixture", "policy", "sx_composite")
	current.Identity.Fingerprint = "new"
	got, err := operations.MatchWorker(expected, []operations.WorkerInfo{old, current}, now)
	if err != nil || got.Fingerprint != "new" {
		t.Fatalf("capable worker not selected: %v", err)
	}
	pinnedOld := operations.PreferredIdentity(expected, []operations.ReleaseChannel{{Kind: "multiband", Current: "old"}})
	if _, err := operations.MatchWorker(pinnedOld, []operations.WorkerInfo{old, current}, now); err == nil {
		t.Fatal("old default release bypassed policy capability")
	}
	// A standalone X QC request remains compatible with the accepted old pool.
	x := operations.Identity{Kind: "multiband", Versions: multiBandVersions("fixture", "policy", "x_qc")}
	if _, err := operations.MatchWorker(x, []operations.WorkerInfo{old}, now); err != nil {
		t.Fatalf("standalone X QC unnecessarily blocked: %v", err)
	}
}

func TestMultiBandPolicyCapabilityRecheck(t *testing.T) {
	for _, field := range []string{"requested_s_radars", "s_qc_policy", "s_qc_policy_complete"} {
		spec := operations.Spec{Kind: "multiband", Identity: operations.Identity{Versions: map[string]string{}},
			Request: operations.JSON(map[string]any{"payload": map[string]any{"mode": "sx_composite", field: nil}})}
		if validateMultiBandPolicyCapability(spec) == nil {
			t.Fatalf("policy field %s accepted without capability", field)
		}
		spec.Identity.Versions["s_qc_policy"] = "future-unknown"
		if validateMultiBandPolicyCapability(spec) == nil {
			t.Fatal("unknown capability accepted")
		}
		spec.Identity.Versions["s_qc_policy"] = "s-qc-policy-v1"
		if err := validateMultiBandPolicyCapability(spec); err != nil {
			t.Fatal(err)
		}
	}
	legacy := operations.Spec{Request: operations.JSON(map[string]any{"payload": map[string]any{"mode": "sx_composite"}})}
	if err := validateMultiBandPolicyCapability(legacy); err != nil {
		t.Fatalf("historical request identity changed: %v", err)
	}
}

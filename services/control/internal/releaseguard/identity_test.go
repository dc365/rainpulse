package releaseguard

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPlannerIdentityExposesDriftWithoutBlessingNewHash(t *testing.T) {
	root := t.TempDir()
	gate := filepath.Join(root, "control", "release-gate.json")
	t.Setenv("RAINPULSE_RELEASE_GATE_FILE", gate)
	config := filepath.Join(root, "qc.yaml")
	if err := os.WriteFile(config, []byte("profile_version: before\n"), 0600); err != nil {
		t.Fatal(err)
	}
	hash, err := FileSHA256(config)
	if err != nil {
		t.Fatal(err)
	}
	if err := PublishPlannerIdentity(config, hash, "realtime_shadow"); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config, []byte("profile_version: after\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := PublishPlannerIdentity(config, hash, "realtime_shadow"); err == nil {
		t.Fatal("changed configuration was admitted")
	}
	body, err := os.ReadFile(filepath.Join(root, "control", "planner-release.json"))
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	if value["loaded_qc_sha256"] != hash || value["current_qc_sha256"] == hash {
		t.Fatal("startup hash was replaced or drift concealed")
	}
	if value["gate_path"] != gate || value["execution_mode"] != "realtime_shadow" {
		t.Fatal("resolved identity lost")
	}
	if strings.Contains(string(body), "profile_version") {
		t.Fatal("configuration contents leaked")
	}
}

func TestFileHashRejectsMissingDirectoryAndOversizedFile(t *testing.T) {
	root := t.TempDir()
	if _, err := FileSHA256(filepath.Join(root, "absent")); err == nil {
		t.Fatal("missing file accepted")
	}
	if _, err := FileSHA256(root); err == nil {
		t.Fatal("directory accepted")
	}
	path := filepath.Join(root, "large")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := file.Truncate(4*1024*1024 + 1); err != nil {
		t.Fatal(err)
	}
	file.Close()
	if _, err := FileSHA256(path); err == nil {
		t.Fatal("oversized file accepted")
	}
}

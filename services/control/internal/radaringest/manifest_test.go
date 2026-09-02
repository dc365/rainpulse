package radaringest

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestLoadManifestExpandsEnvironmentAndAppliesDefaults(t *testing.T) {
	root := t.TempDir()
	if err := os.Setenv("RAINPULSE_TEST_ARRIVAL", filepath.Join(root, "arrival")); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Unsetenv("RAINPULSE_TEST_ARRIVAL") })
	path := filepath.Join(root, "manifest.json")
	payload := `{
  "schema_version": "1.0",
  "profile_version": "test-v1",
  "execution_mode": "realtime_shadow",
  "state_directory": "state",
  "sources": [
    {
      "source_id": "Z9591-Live",
      "radar_id": "Z9591",
      "config_path": "radars/z9591.yaml",
      "arrival_root": "${RAINPULSE_TEST_ARRIVAL}"
    }
  ]
}`
	if err := os.WriteFile(path, []byte(payload), 0o600); err != nil {
		t.Fatal(err)
	}
	manifest, err := LoadManifest(path)
	if err != nil {
		t.Fatal(err)
	}
	if manifest.Interval() != 15*time.Second || manifest.Sources[0].MinimumAge() != 30*time.Second ||
		manifest.Sources[0].Lookback() != 24*time.Hour {
		t.Fatalf("defaults differ: %+v", manifest)
	}
	if manifest.Sources[0].SourceID != "z9591-live" || manifest.Sources[0].RadarID != "z9591" {
		t.Fatalf("IDs were not normalized: %+v", manifest.Sources[0])
	}
	if manifest.StatePath(manifest.Sources[0]) != filepath.Join(root, "state", "z9591-live.json") {
		t.Fatalf("state path differs: %s", manifest.StatePath(manifest.Sources[0]))
	}
}

func TestLoadManifestRejectsUnknownAndDuplicateSources(t *testing.T) {
	root := t.TempDir()
	for name, payload := range map[string]string{
		"unknown":   `{"schema_version":"1.0","profile_version":"x","execution_mode":"realtime_shadow","state_directory":"state","unexpected":true,"sources":[{"source_id":"a","radar_id":"z1","config_path":"a","arrival_root":"a"}]}`,
		"duplicate": `{"schema_version":"1.0","profile_version":"x","execution_mode":"realtime_shadow","state_directory":"state","sources":[{"source_id":"a","radar_id":"z1","config_path":"a","arrival_root":"a"},{"source_id":"b","radar_id":"z1","config_path":"b","arrival_root":"b"}]}`,
	} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(root, name+".json")
			if err := os.WriteFile(path, []byte(payload), 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := LoadManifest(path); err == nil {
				t.Fatal("expected manifest rejection")
			}
		})
	}
}

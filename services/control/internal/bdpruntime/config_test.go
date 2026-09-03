package bdpruntime

import (
	"os"
	"strings"
	"testing"
)

func TestResolveConfigCode(t *testing.T) {
	if got := ResolveConfigCode("custom", "runtime"); got != "custom" {
		t.Fatalf("explicit config code = %q", got)
	}
	if got := ResolveConfigCode("", "runtime"); got != "runtime" {
		t.Fatalf("runtime config code = %q", got)
	}
	if got := ResolveConfigCode("", ""); got != ProgramUniqueCode {
		t.Fatalf("default config code = %q", got)
	}
}

func TestParseAndApplyProgramConfig(t *testing.T) {
	t.Setenv("RAINPULSE_PIPELINE_INTERVAL", "legacy")
	config, err := ParseProgramConfig([]byte(`{
  "schema_version": "1.0",
  "radar_input": {"data_code": "RADA_L2_FMT", "source_index": 0},
  "environment": {
    "common": {"RAINPULSE_OBJECT_STORE_BUCKET": "rainpulse"},
    "orchestrator": {"RAINPULSE_PIPELINE_INTERVAL": "5s"}
  }
}`))
	if err != nil {
		t.Fatal(err)
	}
	keys, err := config.Apply(ComponentOrchestrator)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(keys, ","); got != "RAINPULSE_OBJECT_STORE_BUCKET,RAINPULSE_PIPELINE_INTERVAL" {
		t.Fatalf("applied keys = %q", got)
	}
	if got := config.RadarInput.DataCode; got != DefaultDataCode {
		t.Fatalf("data code = %q", got)
	}
	if got := os.Getenv("RAINPULSE_PIPELINE_INTERVAL"); got != "5s" {
		t.Fatalf("platform value did not override deployment value: %q", got)
	}
}

func TestLoadProgramConfigUsesRequestedCode(t *testing.T) {
	config, err := loadProgramConfig("custom-code", func(code string) (string, error) {
		if code != "custom-code" {
			t.Fatalf("config code = %q", code)
		}
		return `{"schema_version":"1.0","radar_input":{"data_code":"RADA_L2_FMT"}}`, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if config.RadarInput.DataCode != DefaultDataCode {
		t.Fatalf("data code = %q", config.RadarInput.DataCode)
	}
}

func TestProgramConfigRejectsSecretsAndManagedRadarRoot(t *testing.T) {
	for _, name := range []string{"RAINPULSE_DATABASE_PASSWORD", "RAINPULSE_ADMIN_TOKEN", "RAINPULSE_RADAR_INGEST_ROOT"} {
		payload := `{"schema_version":"1.0","environment":{"common":{"` + name + `":"value"}}}`
		if _, err := ParseProgramConfig([]byte(payload)); err == nil {
			t.Fatalf("expected %s to be rejected", name)
		}
	}
}

func TestProgramConfigRejectsInvalidDataCode(t *testing.T) {
	if _, err := ParseProgramConfig([]byte(
		`{"schema_version":"1.0","radar_input":{"data_code":"../../radar"}}`,
	)); err == nil {
		t.Fatal("expected invalid radar data code to be rejected")
	}
}

package bdpruntime

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"
)

type Component string

const (
	ComponentCommon       Component = "common"
	ComponentAPI          Component = "api"
	ComponentWeb          Component = "web"
	ComponentOrchestrator Component = "orchestrator"
	ComponentIngest       Component = "ingest"
)

type Mode string

const (
	ModeOff      Mode = "off"
	ModePrefer   Mode = "prefer"
	ModeRequired Mode = "required"
)

type RadarInputConfig struct {
	DataCode              string `json:"data_code"`
	SourceIndex           int    `json:"source_index"`
	ScanIntervalSeconds   int    `json:"scan_interval_seconds"`
	MinimumFileAgeSeconds int    `json:"minimum_file_age_seconds"`
	LookbackHours         int    `json:"lookback_hours"`
}

type ProgramConfig struct {
	SchemaVersion string                       `json:"schema_version"`
	RadarInput    RadarInputConfig             `json:"radar_input"`
	Environment   map[string]map[string]string `json:"environment"`
}

type ConfigFetcher func(configCode string) (string, error)

type Runtime struct {
	Mode              Mode
	ConfigCode        string
	PlatformAvailable bool
	ConfigLoaded      bool
	Config            ProgramConfig
}

func (runtime Runtime) Required() bool {
	return runtime.Mode == ModeRequired
}

var environmentNamePattern = regexp.MustCompile(`^RAINPULSE_[A-Z0-9_]+$`)
var dataCodePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)

var supportedComponents = map[string]struct{}{
	string(ComponentCommon):       {},
	string(ComponentAPI):          {},
	string(ComponentWeb):          {},
	string(ComponentOrchestrator): {},
	string(ComponentIngest):       {},
}

var protectedEnvironmentFragments = []string{
	"ACCESS_KEY",
	"ADMIN_TOKEN",
	"DATABASE_PASSWORD",
	"MINIO_ROOT",
	"NATS_URL",
	"PASSWORD",
	"SECRET",
	"TOKEN",
}

func DefaultProgramConfig() ProgramConfig {
	return ProgramConfig{
		SchemaVersion: "1.0",
		RadarInput: RadarInputConfig{
			DataCode:              DefaultDataCode,
			SourceIndex:           0,
			ScanIntervalSeconds:   15,
			MinimumFileAgeSeconds: 30,
			LookbackHours:         24,
		},
		Environment: make(map[string]map[string]string),
	}
}

func ParseProgramConfig(payload []byte) (ProgramConfig, error) {
	config := DefaultProgramConfig()
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&config); err != nil {
		return ProgramConfig{}, fmt.Errorf("decode RainPulse ProgramConfig: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return ProgramConfig{}, fmt.Errorf("RainPulse ProgramConfig has trailing JSON values")
	}
	if err := config.Validate(); err != nil {
		return ProgramConfig{}, err
	}
	return config, nil
}

func loadProgramConfig(configCode string, fetcher ConfigFetcher) (ProgramConfig, error) {
	content, err := fetcher(configCode)
	if err != nil {
		return ProgramConfig{}, err
	}
	return ParseProgramConfig([]byte(content))
}

func (config ProgramConfig) Validate() error {
	if strings.TrimSpace(config.SchemaVersion) != "1.0" {
		return fmt.Errorf("RainPulse ProgramConfig schema_version must be 1.0")
	}
	if !dataCodePattern.MatchString(strings.TrimSpace(config.RadarInput.DataCode)) {
		return fmt.Errorf("RainPulse ProgramConfig radar_input.data_code is invalid")
	}
	if config.RadarInput.SourceIndex < 0 {
		return fmt.Errorf("RainPulse ProgramConfig radar_input.source_index cannot be negative")
	}
	if config.RadarInput.ScanIntervalSeconds < 1 || config.RadarInput.ScanIntervalSeconds > 300 {
		return fmt.Errorf("RainPulse ProgramConfig radar_input.scan_interval_seconds is outside 1..300")
	}
	if config.RadarInput.MinimumFileAgeSeconds < 0 || config.RadarInput.MinimumFileAgeSeconds > 3600 {
		return fmt.Errorf("RainPulse ProgramConfig radar_input.minimum_file_age_seconds is outside 0..3600")
	}
	if config.RadarInput.LookbackHours < 1 || config.RadarInput.LookbackHours > 24*31 {
		return fmt.Errorf("RainPulse ProgramConfig radar_input.lookback_hours is outside 1..744")
	}
	for component, variables := range config.Environment {
		if _, ok := supportedComponents[component]; !ok {
			return fmt.Errorf("RainPulse ProgramConfig environment component %q is not supported", component)
		}
		for name := range variables {
			if !environmentNamePattern.MatchString(name) {
				return fmt.Errorf("RainPulse ProgramConfig environment name %q is invalid", name)
			}
			if strings.HasPrefix(name, "RAINPULSE_BDP_") || name == "RAINPULSE_RADAR_INGEST_ROOT" {
				return fmt.Errorf("RainPulse ProgramConfig environment name %q is runtime-managed", name)
			}
			for _, fragment := range protectedEnvironmentFragments {
				if strings.Contains(name, fragment) {
					return fmt.Errorf("RainPulse ProgramConfig must not contain sensitive environment name %q", name)
				}
			}
		}
	}
	return nil
}

func (config ProgramConfig) Apply(component Component) ([]string, error) {
	if _, ok := supportedComponents[string(component)]; !ok || component == ComponentCommon {
		return nil, fmt.Errorf("unsupported RainPulse runtime component %q", component)
	}
	if err := config.Validate(); err != nil {
		return nil, err
	}
	merged := make(map[string]string)
	for name, value := range config.Environment[string(ComponentCommon)] {
		merged[name] = value
	}
	for name, value := range config.Environment[string(component)] {
		merged[name] = value
	}
	keys := make([]string, 0, len(merged))
	for name := range merged {
		keys = append(keys, name)
	}
	sort.Strings(keys)
	for _, name := range keys {
		if err := os.Setenv(name, merged[name]); err != nil {
			return nil, fmt.Errorf("apply RainPulse ProgramConfig environment %s: %w", name, err)
		}
	}
	return keys, nil
}

func ResolveConfigCode(explicit, programName string) string {
	if value := strings.TrimSpace(explicit); value != "" {
		return value
	}
	if value := strings.TrimSpace(programName); value != "" {
		return value
	}
	return DefaultConfigCode
}

func modeFromEnvironment() (Mode, error) {
	value := Mode(strings.ToLower(strings.TrimSpace(os.Getenv("RAINPULSE_BDP_MODE"))))
	if value == "" {
		value = ModePrefer
	}
	switch value {
	case ModeOff, ModePrefer, ModeRequired:
		return value, nil
	default:
		return "", fmt.Errorf("RAINPULSE_BDP_MODE must be off, prefer or required")
	}
}

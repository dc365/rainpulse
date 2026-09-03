package radaringest

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

var sourceIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,63}$`)

// Manifest is the strict runtime contract for multiple live radar arrival
// sources. JSON is intentionally used here: it is unambiguous, supports strict
// unknown-field rejection, and can still be generated from deployment tooling.
type Manifest struct {
	SchemaVersion   string           `json:"schema_version"`
	ProfileVersion  string           `json:"profile_version"`
	ExecutionMode   string           `json:"execution_mode"`
	IntervalSeconds int              `json:"interval_seconds"`
	StateDirectory  string           `json:"state_directory"`
	Sources         []ManifestSource `json:"sources"`
}

type ManifestSource struct {
	SourceID         string `json:"source_id"`
	RadarID          string `json:"radar_id"`
	ConfigPath       string `json:"config_path"`
	ArrivalRoot      string `json:"arrival_root"`
	MinAgeSeconds    int    `json:"minimum_file_age_seconds"`
	LookbackHours    int    `json:"lookback_hours"`
	FileNameContains string `json:"file_name_contains"`
}

func (source ManifestSource) MinimumAge() time.Duration {
	return time.Duration(source.MinAgeSeconds) * time.Second
}

func (source ManifestSource) Lookback() time.Duration {
	return time.Duration(source.LookbackHours) * time.Hour
}

func (manifest Manifest) Interval() time.Duration {
	return time.Duration(manifest.IntervalSeconds) * time.Second
}

func (manifest Manifest) StatePath(source ManifestSource) string {
	return filepath.Join(manifest.StateDirectory, source.SourceID+".json")
}

// WithSourceSettings applies the data root and scanner timing managed by the
// Ruiyun BDP ProgramConfig to every radar source.
func (manifest Manifest) WithSourceSettings(
	root string,
	intervalSeconds int,
	minimumFileAgeSeconds int,
	lookbackHours int,
) (Manifest, error) {
	root = filepath.Clean(strings.TrimSpace(root))
	if !filepath.IsAbs(root) {
		return Manifest{}, fmt.Errorf("radar ingest arrival root must be absolute")
	}
	manifest.IntervalSeconds = intervalSeconds
	manifest.Sources = append([]ManifestSource(nil), manifest.Sources...)
	for index := range manifest.Sources {
		manifest.Sources[index].ArrivalRoot = root
		manifest.Sources[index].MinAgeSeconds = minimumFileAgeSeconds
		manifest.Sources[index].LookbackHours = lookbackHours
	}
	if err := manifest.Validate(); err != nil {
		return Manifest{}, err
	}
	return manifest, nil
}

// LoadManifest expands environment placeholders, resolves relative paths from
// the manifest directory, and rejects ambiguous or duplicate source entries.
func LoadManifest(path string) (Manifest, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return Manifest{}, fmt.Errorf("resolve radar ingest manifest: %w", err)
	}
	payload, err := os.ReadFile(absolute)
	if err != nil {
		return Manifest{}, fmt.Errorf("read radar ingest manifest: %w", err)
	}
	expanded := os.ExpandEnv(string(payload))
	decoder := json.NewDecoder(bytes.NewBufferString(expanded))
	decoder.DisallowUnknownFields()
	var manifest Manifest
	if err := decoder.Decode(&manifest); err != nil {
		return Manifest{}, fmt.Errorf("decode radar ingest manifest: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return Manifest{}, fmt.Errorf("radar ingest manifest has trailing JSON values")
	}
	base := filepath.Dir(absolute)
	manifest.StateDirectory = resolveManifestPath(base, manifest.StateDirectory)
	for index := range manifest.Sources {
		source := &manifest.Sources[index]
		source.SourceID = strings.ToLower(strings.TrimSpace(source.SourceID))
		source.RadarID = strings.ToLower(strings.TrimSpace(source.RadarID))
		source.ConfigPath = resolveManifestPath(base, source.ConfigPath)
		source.ArrivalRoot = resolveManifestPath(base, source.ArrivalRoot)
		source.FileNameContains = strings.TrimSpace(source.FileNameContains)
		if source.FileNameContains == "" {
			source.FileNameContains = source.RadarID
		}
		if source.MinAgeSeconds == 0 {
			source.MinAgeSeconds = 30
		}
		if source.LookbackHours == 0 {
			source.LookbackHours = 24
		}
	}
	if manifest.IntervalSeconds == 0 {
		manifest.IntervalSeconds = 15
	}
	if err := manifest.Validate(); err != nil {
		return Manifest{}, err
	}
	sort.Slice(manifest.Sources, func(left, right int) bool {
		return manifest.Sources[left].RadarID < manifest.Sources[right].RadarID
	})
	return manifest, nil
}

func (manifest Manifest) Validate() error {
	if manifest.SchemaVersion != "1.0" {
		return fmt.Errorf("radar ingest manifest schema must be 1.0")
	}
	if strings.TrimSpace(manifest.ProfileVersion) == "" {
		return fmt.Errorf("radar ingest manifest requires profile_version")
	}
	if manifest.ExecutionMode != "realtime_shadow" && manifest.ExecutionMode != "operational" {
		return fmt.Errorf("radar ingest execution_mode must be realtime_shadow or operational")
	}
	if manifest.IntervalSeconds < 1 || manifest.IntervalSeconds > 300 {
		return fmt.Errorf("radar ingest interval_seconds must be between 1 and 300")
	}
	if !filepath.IsAbs(manifest.StateDirectory) {
		return fmt.Errorf("radar ingest state_directory must resolve to an absolute path")
	}
	if len(manifest.Sources) < 1 {
		return fmt.Errorf("radar ingest manifest requires at least one source")
	}
	seenSources := make(map[string]struct{}, len(manifest.Sources))
	seenRadars := make(map[string]struct{}, len(manifest.Sources))
	for _, source := range manifest.Sources {
		if !sourceIDPattern.MatchString(source.SourceID) || !sourceIDPattern.MatchString(source.RadarID) {
			return fmt.Errorf("radar ingest source or radar ID is invalid")
		}
		if _, exists := seenSources[source.SourceID]; exists {
			return fmt.Errorf("duplicate radar ingest source_id %s", source.SourceID)
		}
		if _, exists := seenRadars[source.RadarID]; exists {
			return fmt.Errorf("duplicate radar ingest radar_id %s", source.RadarID)
		}
		seenSources[source.SourceID] = struct{}{}
		seenRadars[source.RadarID] = struct{}{}
		if !filepath.IsAbs(source.ConfigPath) || !filepath.IsAbs(source.ArrivalRoot) {
			return fmt.Errorf("radar ingest source paths must resolve to absolute paths")
		}
		if source.MinAgeSeconds < 0 || source.MinAgeSeconds > 3600 {
			return fmt.Errorf("radar ingest minimum_file_age_seconds is outside 0..3600")
		}
		if source.LookbackHours < 1 || source.LookbackHours > 24*31 {
			return fmt.Errorf("radar ingest lookback_hours is outside 1..744")
		}
	}
	return nil
}

func resolveManifestPath(base, value string) string {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return ""
	}
	if filepath.IsAbs(trimmed) {
		return filepath.Clean(trimmed)
	}
	return filepath.Clean(filepath.Join(base, trimmed))
}

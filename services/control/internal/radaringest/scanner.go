package radaringest

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const scannerStateSchemaVersion = "1.0"

type fileState struct {
	size      int64
	modified  time.Time
	processed bool
}

type persistedFileState struct {
	SizeBytes int64     `json:"size_bytes"`
	Modified  time.Time `json:"modified_at"`
	Processed bool      `json:"processed"`
}

type persistedScannerState struct {
	SchemaVersion    string                        `json:"schema_version"`
	Root             string                        `json:"root"`
	FilenameContains string                        `json:"filename_contains,omitempty"`
	UpdatedAt        time.Time                     `json:"updated_at"`
	Files            map[string]persistedFileState `json:"files"`
}

type Scanner struct {
	mu               sync.Mutex
	root             string
	minAge           time.Duration
	lookback         time.Duration
	statePath        string
	filenameContains string
	files            map[string]fileState
}

// NewScanner keeps the original in-memory behavior. Real-time ingest should use
// NewPersistentScanner so a process restart does not forget already registered
// arrival files.
func NewScanner(root string, minAge, lookback time.Duration) (*Scanner, error) {
	return NewPersistentScanner(root, minAge, lookback, "")
}

// NewPersistentScanner loads and atomically updates a small discovery ledger.
// The content-addressed raw archive remains the final idempotency boundary; the
// ledger prevents needless re-probing and re-registration after restarts.
func NewPersistentScanner(
	root string,
	minAge time.Duration,
	lookback time.Duration,
	statePath string,
) (*Scanner, error) {
	return NewPersistentScannerWithFilter(root, minAge, lookback, statePath, "")
}

// NewPersistentScannerWithFilter limits discovery to radar files whose base
// name contains filenameContains. It is useful when multiple station folders
// share one date-partitioned arrival root.
func NewPersistentScannerWithFilter(
	root string,
	minAge time.Duration,
	lookback time.Duration,
	statePath string,
	filenameContains string,
) (*Scanner, error) {
	absolute, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve radar arrival root: %w", err)
	}
	info, err := os.Stat(absolute)
	if err != nil || !info.IsDir() {
		return nil, fmt.Errorf("radar arrival root must be an existing directory")
	}
	if minAge < 0 || lookback < 0 {
		return nil, fmt.Errorf("radar ingest durations must not be negative")
	}
	resolvedState := ""
	if strings.TrimSpace(statePath) != "" {
		resolvedState, err = filepath.Abs(statePath)
		if err != nil {
			return nil, fmt.Errorf("resolve radar ingest state path: %w", err)
		}
	}
	scanner := &Scanner{
		root: absolute, minAge: minAge, lookback: lookback,
		statePath: resolvedState, filenameContains: strings.ToLower(strings.TrimSpace(filenameContains)),
		files: make(map[string]fileState),
	}
	if err := scanner.load(); err != nil {
		return nil, err
	}
	return scanner, nil
}

func (scanner *Scanner) Scan(now time.Time) ([]string, error) {
	scanner.mu.Lock()
	defer scanner.mu.Unlock()

	ready := make([]string, 0)
	current := make(map[string]struct{})
	changed := false
	err := filepath.WalkDir(scanner.root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			if path != scanner.root && scanner.skipPartition(path, now) {
				return filepath.SkipDir
			}
			return nil
		}
		if !isRadarFile(path) {
			return nil
		}
		if scanner.filenameContains != "" &&
			!strings.Contains(strings.ToLower(filepath.Base(path)), scanner.filenameContains) {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if scanner.lookback > 0 && now.Sub(info.ModTime()) > scanner.lookback {
			if _, exists := scanner.files[path]; exists {
				delete(scanner.files, path)
				changed = true
			}
			return nil
		}
		current[path] = struct{}{}
		state, known := scanner.files[path]
		unchanged := known && state.size == info.Size() && state.modified.Equal(info.ModTime())
		if !unchanged {
			scanner.files[path] = fileState{size: info.Size(), modified: info.ModTime()}
			changed = true
			return nil
		}
		if state.processed || now.Sub(info.ModTime()) < scanner.minAge {
			return nil
		}
		ready = append(ready, path)
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("scan radar arrival root: %w", err)
	}
	for path := range scanner.files {
		if _, exists := current[path]; !exists {
			delete(scanner.files, path)
			changed = true
		}
	}
	if changed {
		if err := scanner.persist(now.UTC()); err != nil {
			return nil, err
		}
	}
	sort.Strings(ready)
	return ready, nil
}

func (scanner *Scanner) skipPartition(path string, now time.Time) bool {
	if scanner.lookback <= 0 {
		return false
	}
	name := filepath.Base(path)
	cutoff := now.UTC().Add(-scanner.lookback)
	if len(name) == 4 && allDigits(name) {
		year, err := time.Parse("2006", name)
		return err == nil && year.Year() < cutoff.Year()
	}
	if len(name) == 8 && allDigits(name) {
		partition, err := time.ParseInLocation("20060102", name, time.UTC)
		return err == nil && !partition.Add(24*time.Hour).After(cutoff)
	}
	return false
}

func allDigits(value string) bool {
	for _, character := range value {
		if character < '0' || character > '9' {
			return false
		}
	}
	return value != ""
}

func (scanner *Scanner) MarkProcessed(path string) error {
	scanner.mu.Lock()
	defer scanner.mu.Unlock()

	state, exists := scanner.files[path]
	if !exists {
		return fmt.Errorf("radar arrival file is absent from scanner state: %s", path)
	}
	if state.processed {
		return nil
	}
	state.processed = true
	scanner.files[path] = state
	return scanner.persist(time.Now().UTC())
}

func (scanner *Scanner) load() error {
	if scanner.statePath == "" {
		return nil
	}
	payload, err := os.ReadFile(scanner.statePath)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read radar ingest scanner state: %w", err)
	}
	var state persistedScannerState
	if err := json.Unmarshal(payload, &state); err != nil {
		return fmt.Errorf("decode radar ingest scanner state: %w", err)
	}
	if state.SchemaVersion != scannerStateSchemaVersion || state.Root != scanner.root ||
		state.FilenameContains != scanner.filenameContains {
		return fmt.Errorf("radar ingest scanner state identity differs")
	}
	for path, item := range state.Files {
		if !filepath.IsAbs(path) || item.SizeBytes < 0 || item.Modified.IsZero() {
			return fmt.Errorf("radar ingest scanner state contains an invalid file entry")
		}
		scanner.files[path] = fileState{
			size: item.SizeBytes, modified: item.Modified, processed: item.Processed,
		}
	}
	return nil
}

func (scanner *Scanner) persist(now time.Time) error {
	if scanner.statePath == "" {
		return nil
	}
	files := make(map[string]persistedFileState, len(scanner.files))
	for path, state := range scanner.files {
		files[path] = persistedFileState{
			SizeBytes: state.size, Modified: state.modified, Processed: state.processed,
		}
	}
	payload, err := json.MarshalIndent(persistedScannerState{
		SchemaVersion:    scannerStateSchemaVersion,
		Root:             scanner.root,
		FilenameContains: scanner.filenameContains, UpdatedAt: now.UTC(), Files: files,
	}, "", "  ")
	if err != nil {
		return fmt.Errorf("encode radar ingest scanner state: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(scanner.statePath), 0o750); err != nil {
		return fmt.Errorf("create radar ingest scanner state directory: %w", err)
	}
	temporary, err := os.CreateTemp(filepath.Dir(scanner.statePath), ".scanner-state-*.json")
	if err != nil {
		return fmt.Errorf("create radar ingest scanner state: %w", err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(0o600); err != nil {
		_ = temporary.Close()
		return fmt.Errorf("protect radar ingest scanner state: %w", err)
	}
	if _, err := temporary.Write(payload); err != nil {
		_ = temporary.Close()
		return fmt.Errorf("write radar ingest scanner state: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return fmt.Errorf("sync radar ingest scanner state: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close radar ingest scanner state: %w", err)
	}
	if err := os.Rename(temporaryPath, scanner.statePath); err != nil {
		return fmt.Errorf("publish radar ingest scanner state: %w", err)
	}
	return nil
}

func isRadarFile(path string) bool {
	name := strings.ToLower(filepath.Base(path))
	return strings.HasSuffix(name, ".bz2") || strings.HasSuffix(name, ".bin")
}

package radaringest

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type fileState struct {
	size      int64
	modified  time.Time
	processed bool
}

type Scanner struct {
	root     string
	minAge   time.Duration
	lookback time.Duration
	files    map[string]fileState
}

func NewScanner(root string, minAge, lookback time.Duration) (*Scanner, error) {
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
	return &Scanner{
		root: absolute, minAge: minAge, lookback: lookback, files: make(map[string]fileState),
	}, nil
}

func (scanner *Scanner) Scan(now time.Time) ([]string, error) {
	ready := make([]string, 0)
	current := make(map[string]struct{})
	err := filepath.WalkDir(scanner.root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || !isRadarFile(path) {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		current[path] = struct{}{}
		state, known := scanner.files[path]
		unchanged := known && state.size == info.Size() && state.modified.Equal(info.ModTime())
		if !unchanged {
			scanner.files[path] = fileState{size: info.Size(), modified: info.ModTime()}
			return nil
		}
		if state.processed || now.Sub(info.ModTime()) < scanner.minAge {
			return nil
		}
		if scanner.lookback > 0 && now.Sub(info.ModTime()) > scanner.lookback {
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
		}
	}
	sort.Strings(ready)
	return ready, nil
}

func (scanner *Scanner) MarkProcessed(path string) {
	state, exists := scanner.files[path]
	if !exists {
		return
	}
	state.processed = true
	scanner.files[path] = state
}

func isRadarFile(path string) bool {
	name := strings.ToLower(filepath.Base(path))
	return strings.HasSuffix(name, ".bz2") || strings.HasSuffix(name, ".bin")
}

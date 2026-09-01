package radaringest

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestScannerRequiresAnUnchangedObservationAndMarksProcessed(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "volume.bin.bz2")
	if err := os.WriteFile(path, []byte("stable"), 0o600); err != nil {
		t.Fatal(err)
	}
	modified := time.Date(2026, 8, 29, 3, 0, 0, 0, time.UTC)
	if err := os.Chtimes(path, modified, modified); err != nil {
		t.Fatal(err)
	}
	scanner, err := NewScanner(root, 30*time.Second, 24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	now := modified.Add(time.Minute)

	first, err := scanner.Scan(now)
	if err != nil {
		t.Fatal(err)
	}
	second, err := scanner.Scan(now)
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != 0 || len(second) != 1 || second[0] != path {
		t.Fatalf("unexpected stable-file scans: first=%v second=%v", first, second)
	}

	if err := scanner.MarkProcessed(path); err != nil {
		t.Fatal(err)
	}
	third, err := scanner.Scan(now)
	if err != nil {
		t.Fatal(err)
	}
	if len(third) != 0 {
		t.Fatalf("processed file returned again: %v", third)
	}
}

func TestPersistentScannerDoesNotReplayProcessedFilesAfterRestart(t *testing.T) {
	root := t.TempDir()
	statePath := filepath.Join(t.TempDir(), "z9591.json")
	path := filepath.Join(root, "volume.bin.bz2")
	if err := os.WriteFile(path, []byte("stable"), 0o600); err != nil {
		t.Fatal(err)
	}
	modified := time.Date(2026, 8, 29, 3, 0, 0, 0, time.UTC)
	if err := os.Chtimes(path, modified, modified); err != nil {
		t.Fatal(err)
	}
	now := modified.Add(time.Minute)

	first, err := NewPersistentScanner(root, 30*time.Second, 24*time.Hour, statePath)
	if err != nil {
		t.Fatal(err)
	}
	if ready, err := first.Scan(now); err != nil || len(ready) != 0 {
		t.Fatalf("unexpected first observation: ready=%v err=%v", ready, err)
	}
	if ready, err := first.Scan(now); err != nil || len(ready) != 1 {
		t.Fatalf("unexpected stable observation: ready=%v err=%v", ready, err)
	}
	if err := first.MarkProcessed(path); err != nil {
		t.Fatal(err)
	}

	restarted, err := NewPersistentScanner(root, 30*time.Second, 24*time.Hour, statePath)
	if err != nil {
		t.Fatal(err)
	}
	if ready, err := restarted.Scan(now.Add(time.Minute)); err != nil || len(ready) != 0 {
		t.Fatalf("processed file replayed after restart: ready=%v err=%v", ready, err)
	}
}

func TestPersistentScannerResetsLedgerWhenFileChanges(t *testing.T) {
	root := t.TempDir()
	statePath := filepath.Join(t.TempDir(), "z9591.json")
	path := filepath.Join(root, "volume.bin")
	modified := time.Date(2026, 8, 29, 3, 0, 0, 0, time.UTC)
	if err := os.WriteFile(path, []byte("first"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, modified, modified); err != nil {
		t.Fatal(err)
	}
	scanner, err := NewPersistentScanner(root, 0, 24*time.Hour, statePath)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = scanner.Scan(modified.Add(time.Minute))
	ready, err := scanner.Scan(modified.Add(time.Minute))
	if err != nil || len(ready) != 1 {
		t.Fatalf("expected initial file: ready=%v err=%v", ready, err)
	}
	if err := scanner.MarkProcessed(path); err != nil {
		t.Fatal(err)
	}

	changed := modified.Add(time.Minute)
	if err := os.WriteFile(path, []byte("replacement"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, changed, changed); err != nil {
		t.Fatal(err)
	}
	if ready, err := scanner.Scan(changed.Add(time.Minute)); err != nil || len(ready) != 0 {
		t.Fatalf("changed file skipped stability observation: ready=%v err=%v", ready, err)
	}
	if ready, err := scanner.Scan(changed.Add(time.Minute)); err != nil || len(ready) != 1 {
		t.Fatalf("changed file did not re-enter: ready=%v err=%v", ready, err)
	}
}

func TestPersistentScannerFiltersSharedArrivalRootByRadarName(t *testing.T) {
	root := t.TempDir()
	modified := time.Date(2026, 8, 29, 3, 0, 0, 0, time.UTC)
	for _, name := range []string{"Z_RADR_I_Z9591_20260829030000.bin.bz2", "Z_RADR_I_Z9593_20260829030000.bin.bz2"} {
		path := filepath.Join(root, name)
		if err := os.WriteFile(path, []byte(name), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, modified, modified); err != nil {
			t.Fatal(err)
		}
	}
	scanner, err := NewPersistentScannerWithFilter(
		root, 0, 24*time.Hour, filepath.Join(t.TempDir(), "state.json"), "z9591",
	)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = scanner.Scan(modified.Add(time.Minute))
	ready, err := scanner.Scan(modified.Add(time.Minute))
	if err != nil || len(ready) != 1 || !strings.Contains(ready[0], "Z9591") {
		t.Fatalf("shared-root filter differs: ready=%v err=%v", ready, err)
	}
}

func TestScannerExcludesOldHistoryAndIncompleteSuffixes(t *testing.T) {
	root := t.TempDir()
	old := filepath.Join(root, "old.bin")
	partial := filepath.Join(root, "current.part")
	for _, path := range []string{old, partial} {
		if err := os.WriteFile(path, []byte("data"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Date(2026, 8, 29, 4, 0, 0, 0, time.UTC)
	oldTime := now.Add(-25 * time.Hour)
	if err := os.Chtimes(old, oldTime, oldTime); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(partial, now.Add(-time.Minute), now.Add(-time.Minute)); err != nil {
		t.Fatal(err)
	}
	scanner, err := NewScanner(root, 30*time.Second, 24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := scanner.Scan(now); err != nil {
		t.Fatal(err)
	}
	ready, err := scanner.Scan(now)
	if err != nil {
		t.Fatal(err)
	}
	if len(ready) != 0 {
		t.Fatalf("scanner admitted old or incomplete files: %v", ready)
	}
}

func TestScannerPrunesHistoricalDatePartitions(t *testing.T) {
	root := t.TempDir()
	oldDir := filepath.Join(root, "2025", "20250101", "Z9591")
	currentDir := filepath.Join(root, "2026", "20260901", "Z9591")
	for _, directory := range []string{oldDir, currentDir} {
		if err := os.MkdirAll(directory, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Date(2026, 9, 1, 2, 0, 0, 0, time.UTC)
	oldPath := filepath.Join(oldDir, "Z_RADR_I_Z9591_20250101000000_CAP_FMT.bin.bz2")
	currentPath := filepath.Join(currentDir, "Z_RADR_I_Z9591_20260901015500_CAP_FMT.bin.bz2")
	for _, path := range []string{oldPath, currentPath} {
		if err := os.WriteFile(path, []byte("radar"), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, now.Add(-time.Minute), now.Add(-time.Minute)); err != nil {
			t.Fatal(err)
		}
	}
	scanner, err := NewPersistentScannerWithFilter(
		root, 0, 24*time.Hour, filepath.Join(t.TempDir(), "state.json"), "Z9591",
	)
	if err != nil {
		t.Fatal(err)
	}
	if ready, err := scanner.Scan(now); err != nil || len(ready) != 0 {
		t.Fatalf("first scan = %v, %v", ready, err)
	}
	ready, err := scanner.Scan(now.Add(time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if len(ready) != 1 || ready[0] != currentPath {
		t.Fatalf("ready = %v, want only %s", ready, currentPath)
	}
	if _, tracked := scanner.files[oldPath]; tracked {
		t.Fatal("old partition file must not enter persistent scanner state")
	}
}

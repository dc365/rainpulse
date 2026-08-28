package radaringest

import (
	"os"
	"path/filepath"
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

	scanner.MarkProcessed(path)
	third, err := scanner.Scan(now)
	if err != nil {
		t.Fatal(err)
	}
	if len(third) != 0 {
		t.Fatalf("processed file returned again: %v", third)
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

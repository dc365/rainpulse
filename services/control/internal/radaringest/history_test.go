package radaringest

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDiscoverHistoricalFilesRequiresDirectStationAndHeaderIdentity(t *testing.T) {
	root := t.TempDir()
	day := time.Date(2026, 8, 28, 0, 0, 0, 0, time.UTC)
	dateRoot := filepath.Join(root, "2026", "20260828")
	makeFile := func(dir, name, code string) {
		t.Helper()
		path := filepath.Join(dateRoot, dir, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		payload := make([]byte, genericHeaderSize+siteConfigSize+taskConfigSize+cutConfigSize)
		binary.LittleEndian.PutUint32(payload[:4], rstmMagic)
		binary.LittleEndian.PutUint16(payload[4:6], 2)
		binary.LittleEndian.PutUint32(payload[8:12], 1)
		copy(payload[genericHeaderSize:], code)
		binary.LittleEndian.PutUint32(payload[genericHeaderSize+siteConfigSize+176:], 1)
		radial := make([]byte, radialHeaderSize)
		binary.LittleEndian.PutUint32(radial[28:], uint32(day.Add(time.Minute).Unix()))
		payload = append(payload, radial...)
		if err := os.WriteFile(path, payload, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	makeFile("ZF101", "Z_RADR_I_ZF101_20260828000100_O_FMT.bin", "ZF101")
	makeFile(filepath.Join("ZF900", "ZF101"), "Z_RADR_I_ZF101_20260828000100_O_FMT.bin", "ZF101")
	files, err := DiscoverHistoricalFiles(root, "zf101", day, day, day.Add(6*time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 1 || files[0].HeaderCode != "ZF101" || files[0].Start != day.Add(time.Minute) {
		t.Fatalf("files = %+v", files)
	}
	makeFile("ZF101", "Z_RADR_I_ZF101_20260828000200_O_FMT.bin", "OTHER")
	if _, err := DiscoverHistoricalFiles(root, "zf101", day, day, day.Add(6*time.Minute)); err == nil {
		t.Fatal("mismatched header was accepted")
	}
	makeFile("ZF602", "Z_RADR_I_ZF602_20260828000100_O_FMT.bin", "XMKG1")
	aliasFiles, err := DiscoverHistoricalFiles(root, "zf602", day, day, day.Add(6*time.Minute))
	if err != nil || len(aliasFiles) != 1 || aliasFiles[0].HeaderCode != "XMKG1" {
		t.Fatalf("approved header alias not accepted: files=%+v err=%v", aliasFiles, err)
	}
}

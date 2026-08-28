package radaringest

import (
	"bytes"
	"encoding/binary"
	"strings"
	"testing"
	"time"
)

func TestProbeVolumeTimesUsesRadialHeaderUTC(t *testing.T) {
	payload := make([]byte, genericHeaderSize+siteConfigSize+taskConfigSize+cutConfigSize)
	binary.LittleEndian.PutUint32(payload[:4], rstmMagic)
	taskOffset := genericHeaderSize + siteConfigSize
	binary.LittleEndian.PutUint32(payload[taskOffset+176:taskOffset+180], 1)
	for _, sample := range []struct {
		seconds      int64
		microseconds int64
	}{
		{1_700_000_010, 250_000},
		{1_700_000_020, 750_000},
	} {
		header := make([]byte, radialHeaderSize)
		binary.LittleEndian.PutUint32(header[28:32], uint32(sample.seconds))
		binary.LittleEndian.PutUint32(header[32:36], uint32(sample.microseconds))
		binary.LittleEndian.PutUint32(header[40:44], 1)
		moment := make([]byte, momentHeaderSize)
		binary.LittleEndian.PutUint32(moment[16:20], 3)
		payload = append(payload, header...)
		payload = append(payload, moment...)
		payload = append(payload, 1, 2, 3)
	}

	start, end, err := probeVolumeTimes(bytes.NewReader(payload))
	if err != nil {
		t.Fatalf("probe RSTM volume: %v", err)
	}
	if got, want := start, time.Unix(1_700_000_010, 250_000_000).UTC(); !got.Equal(want) {
		t.Fatalf("start = %s, want %s", got, want)
	}
	if got, want := end, time.Unix(1_700_000_020, 750_000_000).UTC(); !got.Equal(want) {
		t.Fatalf("end = %s, want %s", got, want)
	}
}

func TestObjectKeyIsImmutableAndSanitizesFilename(t *testing.T) {
	sha := strings.Repeat("a", 64)
	key, err := ObjectKey(
		"z9598",
		time.Date(2026, 8, 29, 3, 4, 5, 6, time.UTC),
		sha,
		"../unsafe name.bin.bz2",
	)
	if err != nil {
		t.Fatalf("build object key: %v", err)
	}
	want := "radar/raw/z9598/2026/08/29/030405.000000006Z/" + sha + "/unsafe_name.bin.bz2"
	if key != want {
		t.Fatalf("key = %q, want %q", key, want)
	}
}

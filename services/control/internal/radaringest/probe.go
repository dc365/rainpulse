package radaringest

import (
	"compress/bzip2"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"strings"
	"time"
)

const (
	genericHeaderSize = 32
	siteConfigSize    = 128
	taskConfigSize    = 256
	cutConfigSize     = 256
	radialHeaderSize  = 64
	momentHeaderSize  = 32
	rstmMagic         = 0x4D545352
)

func ProbeVolumeTimes(path string) (time.Time, time.Time, error) {
	file, err := os.Open(path)
	if err != nil {
		return time.Time{}, time.Time{}, fmt.Errorf("open radar arrival file: %w", err)
	}
	defer file.Close()
	var reader io.Reader = file
	if strings.HasSuffix(strings.ToLower(path), ".bz2") {
		reader = bzip2.NewReader(file)
	}
	return probeVolumeTimes(reader)
}

func probeVolumeTimes(reader io.Reader) (time.Time, time.Time, error) {
	generic, err := readExact(reader, genericHeaderSize, "generic header")
	if err != nil {
		return time.Time{}, time.Time{}, err
	}
	if binary.LittleEndian.Uint32(generic[:4]) != rstmMagic {
		return time.Time{}, time.Time{}, fmt.Errorf("invalid RSTM magic number")
	}
	if _, err := readExact(reader, siteConfigSize, "site configuration"); err != nil {
		return time.Time{}, time.Time{}, err
	}
	task, err := readExact(reader, taskConfigSize, "task configuration")
	if err != nil {
		return time.Time{}, time.Time{}, err
	}
	cutCount := int(int32(binary.LittleEndian.Uint32(task[176:180])))
	if cutCount < 1 || cutCount > 64 {
		return time.Time{}, time.Time{}, fmt.Errorf("invalid RSTM cut count %d", cutCount)
	}
	if _, err := io.CopyN(io.Discard, reader, int64(cutCount*cutConfigSize)); err != nil {
		return time.Time{}, time.Time{}, fmt.Errorf("read RSTM cut configurations: %w", err)
	}

	var start time.Time
	var end time.Time
	for radialCount := 0; ; radialCount++ {
		header := make([]byte, radialHeaderSize)
		count, readErr := io.ReadFull(reader, header)
		if readErr == io.EOF && count == 0 {
			break
		}
		if readErr != nil {
			return time.Time{}, time.Time{}, fmt.Errorf("read RSTM radial header: %w", readErr)
		}
		seconds := int64(int32(binary.LittleEndian.Uint32(header[28:32])))
		microseconds := int64(int32(binary.LittleEndian.Uint32(header[32:36])))
		momentCount := int(int32(binary.LittleEndian.Uint32(header[40:44])))
		if seconds <= 0 || microseconds < 0 || microseconds >= 1_000_000 || momentCount < 0 || momentCount > 64 {
			return time.Time{}, time.Time{}, fmt.Errorf("invalid RSTM radial time or moment count")
		}
		observed := time.Unix(seconds, microseconds*1_000).UTC()
		if start.IsZero() || observed.Before(start) {
			start = observed
		}
		if end.IsZero() || observed.After(end) {
			end = observed
		}
		for moment := 0; moment < momentCount; moment++ {
			momentHeader, err := readExact(reader, momentHeaderSize, "moment header")
			if err != nil {
				return time.Time{}, time.Time{}, err
			}
			blockLength := int64(int32(binary.LittleEndian.Uint32(momentHeader[16:20])))
			if blockLength < 0 {
				return time.Time{}, time.Time{}, fmt.Errorf("invalid RSTM moment block length")
			}
			if _, err := io.CopyN(io.Discard, reader, blockLength); err != nil {
				return time.Time{}, time.Time{}, fmt.Errorf("read RSTM moment body: %w", err)
			}
		}
	}
	if start.IsZero() {
		return time.Time{}, time.Time{}, fmt.Errorf("RSTM volume has no radials")
	}
	return start, end, nil
}

func readExact(reader io.Reader, size int, label string) ([]byte, error) {
	value := make([]byte, size)
	if _, err := io.ReadFull(reader, value); err != nil {
		return nil, fmt.Errorf("read RSTM %s: %w", label, err)
	}
	return value, nil
}

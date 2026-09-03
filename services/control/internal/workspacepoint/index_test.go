package workspacepoint

import (
	"encoding/binary"
	"math"
	"testing"
)

func fixture(leads int) []byte {
	data := make([]byte, HeaderBytes+2*3*leads*RecordBytes)
	copy(data[:8], magic[:])
	binary.BigEndian.PutUint16(data[8:10], 3)
	binary.BigEndian.PutUint16(data[10:12], 2)
	binary.BigEndian.PutUint16(data[12:14], uint16(leads))
	binary.BigEndian.PutUint16(data[14:16], RecordBytes)
	binary.BigEndian.PutUint64(data[16:24], math.Float64bits(118))
	binary.BigEndian.PutUint64(data[24:32], math.Float64bits(25))
	binary.BigEndian.PutUint64(data[32:40], math.Float64bits(0.01))
	binary.BigEndian.PutUint64(data[40:48], math.Float64bits(0.01))
	for cell := 0; cell < 6; cell++ {
		for lead := 0; lead < leads; lead++ {
			offset := HeaderBytes + (cell*leads+lead)*RecordBytes
			binary.BigEndian.PutUint32(data[offset:offset+4], math.Float32bits(float32(cell+lead)))
			data[offset+4] = 127
		}
	}
	return data
}

func TestFlexiblePointIndexSupportsAnalysisAndForecastLeadCounts(t *testing.T) {
	for _, leads := range []int{1, 12, 24} {
		data := fixture(leads)
		header, err := ParseHeader(data[:HeaderBytes])
		if err != nil {
			t.Fatalf("ParseHeader(%d) error = %v", leads, err)
		}
		if header.LeadCount != leads || header.ExpectedSize() != int64(len(data)) {
			t.Fatalf("unexpected header for %d: %#v", leads, header)
		}
		row, column, longitude, latitude, err := header.Point(118.011, 25.009)
		if err != nil || row != 1 || column != 1 || longitude != 118.01 || latitude != 25.01 {
			t.Fatalf("unexpected point for %d: %d %d %.3f %.3f %v", leads, row, column, longitude, latitude, err)
		}
		offset, _ := header.CellOffset(row, column)
		values, err := header.DecodeCell(data[offset : offset+header.CellBytes()])
		if err != nil || len(values) != leads || !values[0].Valid || *values[0].RainRate != 4 {
			t.Fatalf("unexpected values for %d: %#v, %v", leads, values, err)
		}
	}
}

func TestFlexiblePointIndexPreservesMissing(t *testing.T) {
	data := fixture(1)
	header, _ := ParseHeader(data[:HeaderBytes])
	offset, _ := header.CellOffset(0, 0)
	binary.BigEndian.PutUint32(data[offset:offset+4], math.Float32bits(float32(math.NaN())))
	data[offset+4] = 255
	values, err := header.DecodeCell(data[offset : offset+header.CellBytes()])
	if err != nil || values[0].Valid || values[0].RainRate != nil || values[0].Confidence != nil {
		t.Fatalf("missing point was not preserved: %#v, %v", values, err)
	}
}

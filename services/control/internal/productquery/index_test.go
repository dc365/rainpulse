package productquery

import (
	"encoding/binary"
	"math"
	"testing"
)

func fixtureHeader() []byte {
	data := make([]byte, HeaderBytes)
	copy(data[:8], magic[:])
	binary.BigEndian.PutUint16(data[8:10], 3)
	binary.BigEndian.PutUint16(data[10:12], 2)
	binary.BigEndian.PutUint16(data[12:14], 24)
	binary.BigEndian.PutUint16(data[14:16], RecordBytes)
	binary.BigEndian.PutUint64(data[16:24], math.Float64bits(118))
	binary.BigEndian.PutUint64(data[24:32], math.Float64bits(25))
	binary.BigEndian.PutUint64(data[32:40], math.Float64bits(0.01))
	binary.BigEndian.PutUint64(data[40:48], math.Float64bits(0.01))
	return data
}

func TestPointIndexDecodesValidNoRainAndMissing(t *testing.T) {
	header, err := ParseHeader(fixtureHeader())
	if err != nil {
		t.Fatalf("ParseHeader() error = %v", err)
	}
	cell := make([]byte, header.CellBytes())
	for lead := range header.LeadCount {
		offset := lead * RecordBytes
		binary.BigEndian.PutUint32(cell[offset:offset+4], math.Float32bits(float32(lead)))
		cell[offset+4] = 127
	}
	binary.BigEndian.PutUint32(cell[5:9], math.Float32bits(float32(math.NaN())))
	cell[9] = 255
	values, err := header.DecodeCell(cell)
	if err != nil {
		t.Fatalf("DecodeCell() error = %v", err)
	}
	if !values[0].Valid || values[0].RainRate == nil || *values[0].RainRate != 0 {
		t.Fatalf("valid no-rain was not preserved: %#v", values[0])
	}
	if values[1].Valid || values[1].RainRate != nil {
		t.Fatalf("missing record was not preserved: %#v", values[1])
	}
	row, column, longitude, latitude, err := header.Point(118.011, 25.009)
	if err != nil || row != 1 || column != 1 || longitude != 118.01 || latitude != 25.01 {
		t.Fatalf("unexpected snapped point: row=%d column=%d lon=%f lat=%f err=%v",
			row, column, longitude, latitude, err)
	}
}

func TestPointIndexSummarizesOneLeadWithoutChangingMissing(t *testing.T) {
	header, _ := ParseHeader(fixtureHeader())
	window, err := header.BoundingBox([]float64{118, 25, 118.02, 25.01})
	if err != nil {
		t.Fatalf("BoundingBox() error = %v", err)
	}
	rows := make([][]byte, 2)
	for row := range rows {
		rows[row] = make([]byte, 3*int(header.CellBytes()))
		for column := range 3 {
			for lead := range 24 {
				offset := column*int(header.CellBytes()) + lead*RecordBytes
				binary.BigEndian.PutUint32(
					rows[row][offset:offset+4],
					math.Float32bits(float32(row+column+lead)),
				)
				rows[row][offset+4] = 254
			}
		}
	}
	binary.BigEndian.PutUint32(rows[1][0:4], math.Float32bits(float32(math.NaN())))
	rows[1][4] = 255
	statistics, err := header.SummarizeRows(rows, window, 5)
	if err != nil {
		t.Fatalf("SummarizeRows() error = %v", err)
	}
	if statistics.ValidCount != 5 || statistics.MissingCount != 1 ||
		statistics.Maximum != 3 || statistics.Mean != 1.6 {
		t.Fatalf("unexpected area statistics: %#v", statistics)
	}
}

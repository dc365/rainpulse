package productquery

import (
	"encoding/binary"
	"fmt"
	"math"
)

const (
	HeaderBytes = 64
	RecordBytes = 5
)

var magic = [8]byte{'R', 'P', 'P', 'N', 'T', 'V', '1', 0}

type Header struct {
	Width             int
	Height            int
	LeadCount         int
	RecordBytes       int
	West              float64
	South             float64
	LongitudeInterval float64
	LatitudeInterval  float64
}

type Value struct {
	RainRate   *float32
	Confidence *float32
	Valid      bool
}

type Window struct {
	ColumnStart int
	ColumnEnd   int
	RowStart    int
	RowEnd      int
}

type Statistics struct {
	ValidCount   int64
	MissingCount int64
	Mean         float64
	Maximum      float64
}

func ParseHeader(data []byte) (Header, error) {
	if len(data) != HeaderBytes {
		return Header{}, fmt.Errorf("point index header must contain %d bytes", HeaderBytes)
	}
	var found [8]byte
	copy(found[:], data[:8])
	if found != magic {
		return Header{}, fmt.Errorf("point index magic is invalid")
	}
	header := Header{
		Width:             int(binary.BigEndian.Uint16(data[8:10])),
		Height:            int(binary.BigEndian.Uint16(data[10:12])),
		LeadCount:         int(binary.BigEndian.Uint16(data[12:14])),
		RecordBytes:       int(binary.BigEndian.Uint16(data[14:16])),
		West:              math.Float64frombits(binary.BigEndian.Uint64(data[16:24])),
		South:             math.Float64frombits(binary.BigEndian.Uint64(data[24:32])),
		LongitudeInterval: math.Float64frombits(binary.BigEndian.Uint64(data[32:40])),
		LatitudeInterval:  math.Float64frombits(binary.BigEndian.Uint64(data[40:48])),
	}
	if header.Width <= 0 || header.Height <= 0 || header.LeadCount != 24 ||
		header.RecordBytes != RecordBytes || header.LongitudeInterval <= 0 ||
		header.LatitudeInterval <= 0 || !finite(header.West) || !finite(header.South) {
		return Header{}, fmt.Errorf("point index grid metadata is invalid")
	}
	return header, nil
}

func (header Header) CellBytes() int64 {
	return int64(header.LeadCount * header.RecordBytes)
}

func (header Header) ExpectedSize() int64 {
	return HeaderBytes + int64(header.Width*header.Height)*header.CellBytes()
}

func (header Header) Point(longitude, latitude float64) (int, int, float64, float64, error) {
	if !finite(longitude) || !finite(latitude) {
		return 0, 0, 0, 0, fmt.Errorf("point coordinates must be finite")
	}
	column := int(math.Round((longitude - header.West) / header.LongitudeInterval))
	row := int(math.Round((latitude - header.South) / header.LatitudeInterval))
	if column < 0 || column >= header.Width || row < 0 || row >= header.Height {
		return 0, 0, 0, 0, fmt.Errorf("point is outside the product grid")
	}
	gridLongitude := header.West + float64(column)*header.LongitudeInterval
	gridLatitude := header.South + float64(row)*header.LatitudeInterval
	return row, column, gridLongitude, gridLatitude, nil
}

func (header Header) CellOffset(row, column int) (int64, error) {
	if row < 0 || row >= header.Height || column < 0 || column >= header.Width {
		return 0, fmt.Errorf("point index cell is outside the grid")
	}
	return HeaderBytes + int64(row*header.Width+column)*header.CellBytes(), nil
}

func (header Header) DecodeCell(data []byte) ([]Value, error) {
	if len(data) != int(header.CellBytes()) {
		return nil, fmt.Errorf("point index cell byte length differs")
	}
	values := make([]Value, header.LeadCount)
	for lead := range header.LeadCount {
		offset := lead * header.RecordBytes
		rate := math.Float32frombits(binary.BigEndian.Uint32(data[offset : offset+4]))
		encodedConfidence := data[offset+4]
		if math.IsNaN(float64(rate)) && encodedConfidence == 255 {
			continue
		}
		if math.IsNaN(float64(rate)) || math.IsInf(float64(rate), 0) || rate < 0 ||
			encodedConfidence == 255 {
			return nil, fmt.Errorf("point index contains an invalid value record")
		}
		confidence := float32(encodedConfidence) / 254.0
		value := rate
		values[lead] = Value{RainRate: &value, Confidence: &confidence, Valid: true}
	}
	return values, nil
}

func (header Header) BoundingBox(values []float64) (Window, error) {
	if len(values) != 4 || !finite(values[0]) || !finite(values[1]) ||
		!finite(values[2]) || !finite(values[3]) || values[0] > values[2] ||
		values[1] > values[3] {
		return Window{}, fmt.Errorf("bounding box is invalid")
	}
	east := header.West + float64(header.Width-1)*header.LongitudeInterval
	north := header.South + float64(header.Height-1)*header.LatitudeInterval
	if values[0] < header.West || values[2] > east || values[1] < header.South ||
		values[3] > north {
		return Window{}, fmt.Errorf("bounding box is outside the product grid")
	}
	window := Window{
		ColumnStart: int(math.Ceil((values[0]-header.West)/header.LongitudeInterval - 1e-9)),
		ColumnEnd:   int(math.Floor((values[2]-header.West)/header.LongitudeInterval + 1e-9)),
		RowStart:    int(math.Ceil((values[1]-header.South)/header.LatitudeInterval - 1e-9)),
		RowEnd:      int(math.Floor((values[3]-header.South)/header.LatitudeInterval + 1e-9)),
	}
	if window.ColumnStart > window.ColumnEnd || window.RowStart > window.RowEnd {
		return Window{}, fmt.Errorf("bounding box contains no grid points")
	}
	return window, nil
}

func (header Header) SummarizeRows(
	rows [][]byte,
	window Window,
	leadMinutes int,
) (Statistics, error) {
	if leadMinutes < 5 || leadMinutes > 120 || leadMinutes%5 != 0 {
		return Statistics{}, fmt.Errorf("lead time must be one of 5 through 120 minutes")
	}
	width := window.ColumnEnd - window.ColumnStart + 1
	height := window.RowEnd - window.RowStart + 1
	if len(rows) != height {
		return Statistics{}, fmt.Errorf("point index window row count differs")
	}
	leadIndex := leadMinutes/5 - 1
	var statistics Statistics
	statistics.Maximum = 0
	var sum float64
	for _, row := range rows {
		if len(row) != width*int(header.CellBytes()) {
			return Statistics{}, fmt.Errorf("point index window row byte length differs")
		}
		for column := range width {
			offset := column*int(header.CellBytes()) + leadIndex*header.RecordBytes
			rate := math.Float32frombits(binary.BigEndian.Uint32(row[offset : offset+4]))
			confidence := row[offset+4]
			if math.IsNaN(float64(rate)) && confidence == 255 {
				statistics.MissingCount++
				continue
			}
			if math.IsNaN(float64(rate)) || math.IsInf(float64(rate), 0) || rate < 0 ||
				confidence == 255 {
				return Statistics{}, fmt.Errorf("point index contains an invalid value record")
			}
			statistics.ValidCount++
			sum += float64(rate)
			statistics.Maximum = math.Max(statistics.Maximum, float64(rate))
		}
	}
	if statistics.ValidCount > 0 {
		statistics.Mean = sum / float64(statistics.ValidCount)
	}
	return statistics, nil
}

func finite(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}

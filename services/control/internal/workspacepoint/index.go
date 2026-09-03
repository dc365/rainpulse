package workspacepoint

import (
	"encoding/binary"
	"errors"
	"fmt"
	"math"
)

const (
	HeaderBytes = 64
	RecordBytes = 5
)

var magic = [8]byte{'R', 'P', 'P', 'N', 'T', 'V', '1', 0}

var ErrInvalidIndex = errors.New("workspace point index is invalid")

type Header struct {
	Width                int
	Height               int
	LeadCount            int
	West                 float64
	South                float64
	LongitudeIntervalDeg float64
	LatitudeIntervalDeg  float64
}

type Value struct {
	RainRate   *float32
	Confidence *float32
	Valid      bool
}

func ParseHeader(data []byte) (Header, error) {
	if len(data) < HeaderBytes {
		return Header{}, fmt.Errorf("%w: header is truncated", ErrInvalidIndex)
	}
	var candidate [8]byte
	copy(candidate[:], data[:8])
	width := int(binary.BigEndian.Uint16(data[8:10]))
	height := int(binary.BigEndian.Uint16(data[10:12]))
	leadCount := int(binary.BigEndian.Uint16(data[12:14]))
	recordBytes := int(binary.BigEndian.Uint16(data[14:16]))
	header := Header{
		Width:                width,
		Height:               height,
		LeadCount:            leadCount,
		West:                 math.Float64frombits(binary.BigEndian.Uint64(data[16:24])),
		South:                math.Float64frombits(binary.BigEndian.Uint64(data[24:32])),
		LongitudeIntervalDeg: math.Float64frombits(binary.BigEndian.Uint64(data[32:40])),
		LatitudeIntervalDeg:  math.Float64frombits(binary.BigEndian.Uint64(data[40:48])),
	}
	if candidate != magic || recordBytes != RecordBytes || width < 1 || height < 1 ||
		leadCount < 1 || leadCount > 1440 || !finite(header.West) || !finite(header.South) ||
		!finite(header.LongitudeIntervalDeg) || !finite(header.LatitudeIntervalDeg) ||
		header.LongitudeIntervalDeg <= 0 || header.LatitudeIntervalDeg <= 0 {
		return Header{}, fmt.Errorf("%w: header identity differs", ErrInvalidIndex)
	}
	if header.ExpectedSize() < HeaderBytes {
		return Header{}, fmt.Errorf("%w: dimensions overflow", ErrInvalidIndex)
	}
	return header, nil
}

func (header Header) ExpectedSize() int64 {
	return int64(HeaderBytes) + int64(header.Width)*int64(header.Height)*header.CellBytes()
}

func (header Header) CellBytes() int64 {
	return int64(header.LeadCount * RecordBytes)
}

func (header Header) Point(longitude, latitude float64) (row, column int, gridLongitude, gridLatitude float64, err error) {
	if !finite(longitude) || !finite(latitude) {
		return 0, 0, 0, 0, fmt.Errorf("%w: coordinates are not finite", ErrInvalidIndex)
	}
	column = int(math.Round((longitude - header.West) / header.LongitudeIntervalDeg))
	row = int(math.Round((latitude - header.South) / header.LatitudeIntervalDeg))
	if row < 0 || row >= header.Height || column < 0 || column >= header.Width {
		return 0, 0, 0, 0, fmt.Errorf("%w: point lies outside the grid", ErrInvalidIndex)
	}
	gridLongitude = header.West + float64(column)*header.LongitudeIntervalDeg
	gridLatitude = header.South + float64(row)*header.LatitudeIntervalDeg
	return row, column, gridLongitude, gridLatitude, nil
}

func (header Header) CellOffset(row, column int) (int64, error) {
	if row < 0 || row >= header.Height || column < 0 || column >= header.Width {
		return 0, fmt.Errorf("%w: cell lies outside the grid", ErrInvalidIndex)
	}
	cell := int64(row*header.Width + column)
	return int64(HeaderBytes) + cell*header.CellBytes(), nil
}

func (header Header) DecodeCell(data []byte) ([]Value, error) {
	if int64(len(data)) != header.CellBytes() {
		return nil, fmt.Errorf("%w: cell byte length differs", ErrInvalidIndex)
	}
	values := make([]Value, header.LeadCount)
	for index := range values {
		offset := index * RecordBytes
		rate := math.Float32frombits(binary.BigEndian.Uint32(data[offset : offset+4]))
		confidenceCode := data[offset+4]
		if confidenceCode == 255 || math.IsNaN(float64(rate)) {
			values[index] = Value{Valid: false}
			continue
		}
		if math.IsInf(float64(rate), 0) || rate < 0 {
			return nil, fmt.Errorf("%w: rain rate is invalid", ErrInvalidIndex)
		}
		confidenceValue := float32(confidenceCode) / 254
		rateValue := rate
		values[index] = Value{
			RainRate: &rateValue, Confidence: &confidenceValue, Valid: true,
		}
	}
	return values, nil
}

func finite(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}
